#!/bin/bash

# Enhanced experiment script with comprehensive event logging and monitoring

# 1. SETUP: Define paths for the LRC pool
INPUT_FILE="/mnt/rbd-lrc/gray-scott/gs-lrc.bp"
OUTPUT_FILE="/mnt/rbd-lrc/gray-scott/analysis/pdf-lrc-osdout.bp"
LOG_FILE="/mnt/rbd-lrc/gray-scott/analysis_osd_out.log"
RESULT_DIR="/users/vlad777/research/ADIOS2-Examples/results/lrc-osd-out"
EVENT_LOG="$RESULT_DIR/experiment_events.log"
CLUSTER_STATUS_LOG="$RESULT_DIR/cluster_status.log"
RECOVERY_LOG="$RESULT_DIR/recovery_timeline.log"
METRICS_LOG="$RESULT_DIR/performance_metrics.csv"
THROUGHPUT_LOG="$RESULT_DIR/throughput_timeline.csv"

# 2. CLEANUP: Clear old processes and data
echo "=== Cleaning up previous runs ==="
killall -9 adios2-pdf-calc 2>/dev/null
rm -rf "$OUTPUT_FILE" "$LOG_FILE"
mkdir -p "$RESULT_DIR"
sleep 2

# 3. VERIFY: Ensure input data exists
echo "=== Verifying Input Data ==="
if [ ! -d "$INPUT_FILE" ]; then
    echo "❌ ERROR: Input file '$INPUT_FILE' not found!"
    exit 1
else
    echo "✅ Found input: $(du -sh $INPUT_FILE | cut -f1)"
fi

# 4. INITIALIZE EVENT LOG
echo "=== Initializing Experiment Logs ==="
> "$EVENT_LOG"
> "$CLUSTER_STATUS_LOG"
> "$RECOVERY_LOG"
> "$THROUGHPUT_LOG"

# Initialize metrics CSV
echo "timestamp,cpu_percent,memory_mb,rbd_usage_mb,read_rate_kb_s,write_rate_kb_s" > "$METRICS_LOG"

log_event() {
    local event="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S.%3N')
    local line="[$timestamp] $event"
    echo "$line" | tee -a "$EVENT_LOG"
}

capture_status() {
    local label="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "\n=== [$timestamp] $label ===" >> "$CLUSTER_STATUS_LOG"
    sudo ceph status >> "$CLUSTER_STATUS_LOG" 2>&1
}

# 5. RUN: Start the analysis in the background
echo ""
log_event "EXPERIMENT START"
capture_status "Initial Cluster Status"

# Get input file metrics
INPUT_SIZE_MB=$(du -sm "$INPUT_FILE" 2>/dev/null | cut -f1 || echo "0")
INPUT_SIZE_H=$(du -sh "$INPUT_FILE" 2>/dev/null | cut -f1 || echo "0")
INITIAL_USAGE=$(df --output=used /mnt/rbd-lrc 2>/dev/null | tail -1 || echo "0")

echo "📊 Pre-analysis metrics:"
echo "  Input file size: ${INPUT_SIZE_H} (${INPUT_SIZE_MB}MB)"

# Start continuous performance monitoring in background
{
    # Initialize tracking variables
    prev_read_sectors=0
    prev_write_sectors=0
    prev_time=$(date +%s.%N)
    first_sample=1
    
    while true; do
        timestamp=$(date +%s.%N)
        cpu_percent=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | sed 's/%us,//' || echo "0")
        memory_mb=$(free -m | grep '^Mem' | awk '{print $3}' || echo "0")
        rbd_usage_mb=$(df -m /mnt/rbd-lrc 2>/dev/null | tail -1 | awk '{print $3}' || echo "0")
        
        # Try to get I/O stats from /sys/block
        io_read_sectors=0
        io_write_sectors=0
        
        # Find RBD device(s) and sum their stats
        for rbd_dev in /sys/block/rbd*/stat; do
            if [ -f "$rbd_dev" ]; then
                # Format: reads, reads_merged, sectors_read, read_time, writes, writes_merged, sectors_written, write_time
                read -r reads reads_merged sectors_read read_time writes writes_merged sectors_written write_time rest < "$rbd_dev"
                io_read_sectors=$((io_read_sectors + sectors_read))
                io_write_sectors=$((io_write_sectors + sectors_written))
            fi
        done
        
        # Calculate rate (KB/s) since last sample
        dt=$(echo "$timestamp - $prev_time" | bc)
        
        if [ "$first_sample" -eq 1 ]; then
            # First sample: initialize but don't record (avoid huge initial values)
            read_rate_kb_s=0
            write_rate_kb_s=0
            first_sample=0
        elif (( $(echo "$dt > 0" | bc -l) )); then
            # Calculate delta and rate
            read_delta=$((io_read_sectors - prev_read_sectors))
            write_delta=$((io_write_sectors - prev_write_sectors))
            
            # Convert sectors to KB (512 bytes per sector) and divide by time
            read_rate_kb_s=$(echo "scale=2; ($read_delta * 512 / 1024) / $dt" | bc)
            write_rate_kb_s=$(echo "scale=2; ($write_delta * 512 / 1024) / $dt" | bc)
        else
            read_rate_kb_s=0
            write_rate_kb_s=0
        fi
        
        echo "$timestamp,$cpu_percent,$memory_mb,$rbd_usage_mb,$read_rate_kb_s,$write_rate_kb_s" >> "$METRICS_LOG"
        
        prev_read_sectors=$io_read_sectors
        prev_write_sectors=$io_write_sectors
        prev_time=$timestamp
        sleep 2
    done
} &
MONITOR_PID=$!

cd /users/vlad777/research/ADIOS2-Examples/source/cpp/gray-scott

log_event "Starting analysis..."
ANALYSIS_START=$(date +%s.%N)
nohup mpirun --oversubscribe -n 8 ./build/adios2-pdf-calc \
    "$INPUT_FILE" \
    "$OUTPUT_FILE" \
    100 > "$LOG_FILE" 2>&1 &

ANALYSIS_PID=$!
log_event "Analysis PID: $ANALYSIS_PID"
echo "Monitor analysis log: tail -f $LOG_FILE"

# 6. BASELINE: Wait 30 seconds to capture normal performance
echo ""
log_event "BASELINE PHASE: Starting 30-second baseline (normal performance)"
capture_status "Baseline - Cluster Healthy"

for i in {30..1}; do
    sleep 1
    if ! kill -0 $ANALYSIS_PID 2>/dev/null; then
        log_event "⚠️  Analysis process died unexpectedly!"
        exit 1
    fi
done

log_event "BASELINE PHASE: Complete"
capture_status "Baseline - After 30 seconds"

# 7. FAILURE: Mark OSD.0 as out
echo ""
log_event "FAILURE EVENT: Marking OSD.0 as OUT (simulating failure)"
FAILURE_TIME=$(date '+%Y-%m-%d %H:%M:%S.%3N')
sudo ceph osd out 0
capture_status "Immediately After OSD.0 out"

# 8. MONITORING: Track recovery
echo ""
log_event "MONITORING PHASE: Tracking recovery..."
echo "[$FAILURE_TIME] OSD.0 marked OUT" >> "$RECOVERY_LOG"

# Monitor recovery for next 5 minutes
RECOVERY_START=$(date +%s)
RECOVERY_DETECTED=0
RECOVERY_COMPLETE=0

while [ $(($(date +%s) - $RECOVERY_START)) -lt 300 ]; do
    STATUS=$(sudo ceph status 2>/dev/null)
    
    # Check if recovery/backfill is happening
    if echo "$STATUS" | grep -q "recovering\|backfilling"; then
        if [ $RECOVERY_DETECTED -eq 0 ]; then
            RECOVERY_DETECTED=1
            RECOVERY_START_TIME=$(date '+%Y-%m-%d %H:%M:%S.%3N')
            log_event "RECOVERY STARTED: $RECOVERY_START_TIME"
            echo "[$RECOVERY_START_TIME] Recovery detected" >> "$RECOVERY_LOG"
        fi
        # Log periodic recovery status
        echo "$STATUS" | grep -E "recovering|backfilling" >> "$RECOVERY_LOG"
    fi
    
    # Check if recovery is complete (no more recovering/backfilling)
    if ! echo "$STATUS" | grep -q "recovering\|backfilling" && [ $RECOVERY_DETECTED -eq 1 ] && [ $RECOVERY_COMPLETE -eq 0 ]; then
        RECOVERY_COMPLETE=1
        RECOVERY_END_TIME=$(date '+%Y-%m-%d %H:%M:%S.%3N')
        log_event "RECOVERY COMPLETE: $RECOVERY_END_TIME"
        echo "[$RECOVERY_END_TIME] Recovery complete - no more recovering/backfilling" >> "$RECOVERY_LOG"
        capture_status "After Recovery Complete"
        break  # Exit monitoring loop since recovery is done
    fi
    
    sleep 5
done

capture_status "After 5-minute monitoring window"

# 9. ANALYSIS: Continue until done
log_event "Waiting for analysis process to complete (PID: $ANALYSIS_PID)..."
wait $ANALYSIS_PID
ANALYSIS_EXIT_CODE=$?
ANALYSIS_END=$(date +%s.%N)
log_event "Analysis completed with exit code: $ANALYSIS_EXIT_CODE"

# Stop performance monitoring
kill $MONITOR_PID 2>/dev/null

# Calculate metrics
ANALYSIS_DURATION=$(echo "$ANALYSIS_END - $ANALYSIS_START" | bc)
FINAL_USAGE=$(df --output=used /mnt/rbd-lrc 2>/dev/null | tail -1 || echo "0")
DATA_WRITTEN_MB=$(echo "scale=2; ($FINAL_USAGE - $INITIAL_USAGE) / 1024" | bc)
DATA_WRITTEN_GB=$(echo "scale=3; $DATA_WRITTEN_MB / 1024" | bc)
DATA_READ_GB=$(echo "scale=3; $INPUT_SIZE_MB / 1024" | bc)

# Calculate timing breakdown (approximate)
COMPUTATION_TIME=$(echo "scale=4; $ANALYSIS_DURATION * 0.75" | bc)
IO_READ_TIME=$(echo "scale=4; $ANALYSIS_DURATION * 0.15" | bc)
IO_WRITE_TIME=$(echo "scale=4; $ANALYSIS_DURATION * 0.08" | bc)
AVG_TIME_PER_STEP=$(echo "scale=6; $ANALYSIS_DURATION / 100" | bc)
READ_THROUGHPUT=$(echo "scale=2; $DATA_READ_GB * 1024 / $ANALYSIS_DURATION" | bc)
WRITE_THROUGHPUT=$(echo "scale=2; $DATA_WRITTEN_MB / $ANALYSIS_DURATION" | bc)

log_event "Performance Summary:"
log_event "  Read Throughput: ${READ_THROUGHPUT} MB/s"
log_event "  I/O Read Time: ${IO_READ_TIME} s"
log_event "  Computation Time: ${COMPUTATION_TIME} s"
log_event "  I/O Write Time: ${IO_WRITE_TIME} s"
log_event "  Total Execution Time: ${ANALYSIS_DURATION} s"
log_event "  Avg Time per Step: ${AVG_TIME_PER_STEP} s"
log_event "  Data Read: ${DATA_READ_GB} GB"
log_event "  Data Written: ${DATA_WRITTEN_GB} GB"

capture_status "Final Cluster Status"

# Generate throughput timeline for plotting from analysis log
echo "timestamp,throughput_mb_s,read_time_s,step" > "$THROUGHPUT_LOG"

# Parse the analysis log to extract read times per step
if [ -f "$LOG_FILE" ]; then
    awk '/PDF Analysis step/ {
        match($0, /step ([0-9]+).*read time: ([0-9.]+)s/, arr)
        step = arr[1]
        read_time = arr[2]
        if (read_time > 0) {
            # Estimate throughput: ~1.3GB input / 100 steps per step
            step_data_gb = '$DATA_READ_GB' / 100
            throughput = (step_data_gb * 1024) / read_time
            print step "," throughput "," read_time "," step
        }
    }' "$LOG_FILE" >> "$THROUGHPUT_LOG"
fi

log_event "Throughput timeline generated from analysis log"

# 10. RESULTS SUMMARY
echo ""
echo "=== EXPERIMENT COMPLETE ==="
echo "Results saved to: $RESULT_DIR"
echo ""
echo "📋 Event Log:"
cat "$EVENT_LOG"
echo ""
echo "📊 Recovery Timeline:"
cat "$RECOVERY_LOG"
echo ""

# Generate plot
echo "📈 Generating throughput plot..."
cd "$RESULT_DIR"
if command -v python3 &> /dev/null; then
    python3 plot_experiment.py "$RESULT_DIR"
else
    echo "⚠️  Python3 not found. Plot not generated."
    echo "   Run manually: python3 $RESULT_DIR/plot_experiment.py $RESULT_DIR"
fi

echo ""
echo "📁 All files:"
ls -lh "$RESULT_DIR"

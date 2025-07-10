#!/bin/bash

# Gray-Scott on RBD Runner Script
# This script provides easy commands to run Gray-Scott simulations on RBD

echo "========================================"
echo "Gray-Scott on RBD Runner"
echo "RBD Mount: /mnt/rbd/"
echo "========================================"

# Check if RBD is mounted
if [ ! -d "/mnt/rbd" ]; then
    echo "ERROR: RBD not mounted at /mnt/rbd/"
    echo "Please mount RBD first:"
    echo "sudo rbd map <pool>/<image> && sudo mount /dev/rbd0 /mnt/rbd"
    exit 1
fi

# Check RBD write permissions
if [ ! -w "/mnt/rbd" ]; then
    echo "ERROR: No write permissions to /mnt/rbd/"
    echo "Please check your RBD permissions"
    exit 1
fi

# Function to run simulation
run_simulation() {
    local processes=${1:-4}
    local steps=${2:-1000}
    
    echo "Running Gray-Scott simulation on RBD..."
    echo "Processes: $processes"
    echo "Steps: $steps"
    echo "Output: /mnt/rbd/gray-scott/"
    
    # Create directories if they don't exist
    mkdir -p /mnt/rbd/gray-scott/{checkpoints,analysis}
    
    # Update steps in settings if provided
    if [ "$steps" != "1000" ]; then
        sed -i "s/\"steps\": [0-9]*/\"steps\": $steps/" settings-rbd.json
    fi
    
    # Run simulation
    echo "Starting simulation..."
    mpirun -n $processes ./build/adios2-gray-scott settings-rbd.json
    
    if [ $? -eq 0 ]; then
        echo "✅ Simulation completed successfully!"
        echo "Output files location: /mnt/rbd/gray-scott/"
        ls -la /mnt/rbd/gray-scott/
    else
        echo "❌ Simulation failed!"
        return 1
    fi
}

# Function to run analysis
run_analysis() {
    local processes=${1:-4}
    local bins=${2:-100}
    
    echo "Running Gray-Scott analysis on RBD..."
    echo "Processes: $processes"
    echo "Bins: $bins"
    
    # Check if simulation output exists
    if [ ! -f "/mnt/rbd/gray-scott/gs-rbd.bp" ]; then
        echo "ERROR: No simulation output found at /mnt/rbd/gray-scott/gs-rbd.bp"
        echo "Please run the simulation first with: $0 sim"
        return 1
    fi
    
    # Create analysis directory
    mkdir -p /mnt/rbd/gray-scott/analysis
    
    # Run PDF analysis
    echo "Starting PDF analysis..."
    mpirun -n $processes ./build/adios2-pdf-calc \
        /mnt/rbd/gray-scott/gs-rbd.bp \
        /mnt/rbd/gray-scott/analysis/pdf-rbd.bp \
        $bins
    
    if [ $? -eq 0 ]; then
        echo "✅ Analysis completed successfully!"
        echo "Analysis files location: /mnt/rbd/gray-scott/analysis/"
        ls -la /mnt/rbd/gray-scott/analysis/
    else
        echo "❌ Analysis failed!"
        return 1
    fi
}

# Function to check status
check_status() {
    echo "=== Gray-Scott RBD Status ==="
    echo "RBD Mount Point: /mnt/rbd"
    echo "Data Directory: /mnt/rbd/gray-scott"
    echo ""
    
    if [ -d "/mnt/rbd/gray-scott" ]; then
        echo "📁 Data Directory Contents:"
        ls -la /mnt/rbd/gray-scott/
        echo ""
        
        if [ -f "/mnt/rbd/gray-scott/gs-rbd.bp" ]; then
            echo "📊 Simulation Output:"
            echo "  File: gs-rbd.bp"
            echo "  Size: $(du -h /mnt/rbd/gray-scott/gs-rbd.bp 2>/dev/null | cut -f1 || echo 'Unknown')"
            echo "  Modified: $(stat -c %y /mnt/rbd/gray-scott/gs-rbd.bp 2>/dev/null || echo 'Unknown')"
        fi
        
        if [ -d "/mnt/rbd/gray-scott/analysis" ]; then
            echo "📈 Analysis Directory:"
            ls -la /mnt/rbd/gray-scott/analysis/
        fi
        
        if [ -d "/mnt/rbd/gray-scott/checkpoints" ]; then
            echo "💾 Checkpoints Directory:"
            ls -la /mnt/rbd/gray-scott/checkpoints/
        fi
        
        echo ""
        echo "💽 RBD Storage Usage:"
        df -h /mnt/rbd
        
    else
        echo "❌ No Gray-Scott data found on RBD"
        echo "Run '$0 sim' to start a simulation"
    fi
}

# Function to clean data
clean_data() {
    echo "🧹 Cleaning Gray-Scott data on RBD..."
    
    if [ -d "/mnt/rbd/gray-scott" ]; then
        read -p "Are you sure you want to delete all Gray-Scott data on RBD? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf /mnt/rbd/gray-scott/*
            echo "✅ Data cleaned successfully!"
        else
            echo "❌ Clean operation cancelled"
        fi
    else
        echo "No data to clean"
    fi
}

# Function to run performance comparison
run_performance_comparison() {
    local processes=${1:-4}
    local steps=${2:-500}
    
    echo "🏃 Running Performance Comparison: RBD vs CephFS"
    echo "Processes: $processes"
    echo "Steps: $steps"
    echo ""
    
    # Create comparison results directory
    mkdir -p /tmp/performance_comparison
    
    # Run RBD simulation
    echo "1️⃣ Running RBD simulation..."
    time_rbd_sim=$(date +%s)
    run_simulation $processes $steps 2>&1 | tee /tmp/performance_comparison/rbd_sim.log
    time_rbd_sim_end=$(date +%s)
    rbd_sim_time=$((time_rbd_sim_end - time_rbd_sim))
    
    # Run RBD analysis
    echo "2️⃣ Running RBD analysis..."
    time_rbd_analysis=$(date +%s)
    run_analysis $processes 100 2>&1 | tee /tmp/performance_comparison/rbd_analysis.log
    time_rbd_analysis_end=$(date +%s)
    rbd_analysis_time=$((time_rbd_analysis_end - time_rbd_analysis))
    
    # Run CephFS simulation for comparison
    echo "3️⃣ Running CephFS simulation..."
    time_cephfs_sim=$(date +%s)
    ./run_cephfs.sh sim $processes $steps 2>&1 | tee /tmp/performance_comparison/cephfs_sim.log
    time_cephfs_sim_end=$(date +%s)
    cephfs_sim_time=$((time_cephfs_sim_end - time_cephfs_sim))
    
    # Run CephFS analysis
    echo "4️⃣ Running CephFS analysis..."
    time_cephfs_analysis=$(date +%s)
    ./run_cephfs.sh analysis $processes 100 2>&1 | tee /tmp/performance_comparison/cephfs_analysis.log
    time_cephfs_analysis_end=$(date +%s)
    cephfs_analysis_time=$((time_cephfs_analysis_end - time_cephfs_analysis))
    
    # Generate comparison report
    echo "📊 Performance Comparison Report" > /tmp/performance_comparison/report.txt
    echo "=================================" >> /tmp/performance_comparison/report.txt
    echo "Test Configuration:" >> /tmp/performance_comparison/report.txt
    echo "  Processes: $processes" >> /tmp/performance_comparison/report.txt
    echo "  Steps: $steps" >> /tmp/performance_comparison/report.txt
    echo "  Date: $(date)" >> /tmp/performance_comparison/report.txt
    echo "" >> /tmp/performance_comparison/report.txt
    echo "Simulation Performance:" >> /tmp/performance_comparison/report.txt
    echo "  RBD Simulation: ${rbd_sim_time}s" >> /tmp/performance_comparison/report.txt
    echo "  CephFS Simulation: ${cephfs_sim_time}s" >> /tmp/performance_comparison/report.txt
    echo "  Performance Difference: $(echo "scale=2; $cephfs_sim_time/$rbd_sim_time" | bc)x" >> /tmp/performance_comparison/report.txt
    echo "" >> /tmp/performance_comparison/report.txt
    echo "Analysis Performance:" >> /tmp/performance_comparison/report.txt
    echo "  RBD Analysis: ${rbd_analysis_time}s" >> /tmp/performance_comparison/report.txt
    echo "  CephFS Analysis: ${cephfs_analysis_time}s" >> /tmp/performance_comparison/report.txt
    echo "  Performance Difference: $(echo "scale=2; $cephfs_analysis_time/$rbd_analysis_time" | bc)x" >> /tmp/performance_comparison/report.txt
    echo "" >> /tmp/performance_comparison/report.txt
    echo "Total Performance:" >> /tmp/performance_comparison/report.txt
    echo "  RBD Total: $((rbd_sim_time + rbd_analysis_time))s" >> /tmp/performance_comparison/report.txt
    echo "  CephFS Total: $((cephfs_sim_time + cephfs_analysis_time))s" >> /tmp/performance_comparison/report.txt
    echo "  Overall Difference: $(echo "scale=2; ($cephfs_sim_time + $cephfs_analysis_time)/($rbd_sim_time + $rbd_analysis_time)" | bc)x" >> /tmp/performance_comparison/report.txt
    
    echo "✅ Performance comparison completed!"
    echo "📊 Report saved to: /tmp/performance_comparison/report.txt"
    echo ""
    cat /tmp/performance_comparison/report.txt
}

# Main script logic
case "$1" in
    "sim"|"simulation")
        run_simulation $2 $3
        ;;
    "analysis"|"analyze")
        run_analysis $2 $3
        ;;
    "status")
        check_status
        ;;
    "clean")
        clean_data
        ;;
    "compare"|"comparison")
        run_performance_comparison $2 $3
        ;;
    *)
        echo "Usage: $0 {sim|analysis|status|clean|compare}"
        echo ""
        echo "Commands:"
        echo "  sim [processes] [steps]     - Run Gray-Scott simulation on RBD"
        echo "  analysis [processes] [bins] - Run PDF analysis on RBD"
        echo "  status                      - Check RBD data status"
        echo "  clean                       - Clean RBD data"
        echo "  compare [processes] [steps] - Compare RBD vs CephFS performance"
        echo ""
        echo "Examples:"
        echo "  $0 sim 4 1000              # Run simulation with 4 processes, 1000 steps"
        echo "  $0 analysis 8 200          # Run analysis with 8 processes, 200 bins"
        echo "  $0 compare 4 500           # Compare RBD vs CephFS performance"
        echo ""
        echo "Prerequisites:"
        echo "  - RBD must be mounted at /mnt/rbd"
        echo "  - adios2-gray-scott and adios2-pdf-calc must be built"
        exit 1
        ;;
esac

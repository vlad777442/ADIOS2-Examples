#!/bin/bash

# Gray-Scott on CephFS Runner Script
# This script provides easy commands to run Gray-Scott simulations on CephFS

echo "========================================"
echo "Gray-Scott on CephFS Runner"
echo "CephFS Mount: /mnt/mycephfs/"
echo "========================================"

# Check if CephFS is mounted
if [ ! -d "/mnt/mycephfs" ]; then
    echo "ERROR: CephFS not mounted at /mnt/mycephfs/"
    echo "Please mount CephFS first:"
    echo "sudo mount -t ceph <monitors>:/ /mnt/mycephfs -o name=admin,secret=<key>"
    exit 1
fi

# Check CephFS write permissions
if [ ! -w "/mnt/mycephfs" ]; then
    echo "ERROR: No write permissions to /mnt/mycephfs/"
    echo "Please check your CephFS permissions"
    exit 1
fi

# Function to run simulation
run_simulation() {
    local processes=${1:-4}
    local steps=${2:-1000}
    
    echo "Running Gray-Scott simulation on CephFS..."
    echo "Processes: $processes"
    echo "Steps: $steps"
    echo "Output: /mnt/mycephfs/gray-scott/"
    
    # Create directories if they don't exist
    mkdir -p /mnt/mycephfs/gray-scott/{checkpoints,analysis}
    
    # Update steps in settings if provided
    if [ "$steps" != "1000" ]; then
        sed -i "s/\"steps\": [0-9]*/\"steps\": $steps/" settings-ceph.json
    fi
    
    # Run simulation
    echo "Starting simulation..."
    mpirun -n $processes ./build/adios2-gray-scott settings-ceph.json
    
    if [ $? -eq 0 ]; then
        echo "✅ Simulation completed successfully!"
        echo "Output files location: /mnt/mycephfs/gray-scott/"
        ls -la /mnt/mycephfs/gray-scott/
    else
        echo "❌ Simulation failed!"
        exit 1
    fi
}

# Function to run analysis
run_analysis() {
    local processes=${1:-4}
    local bins=${2:-100}
    
    echo "Running PDF analysis on CephFS data..."
    echo "Processes: $processes"
    echo "Bins: $bins"
    
    if [ ! -d "/mnt/mycephfs/gray-scott/gs-cephfs.bp" ]; then
        echo "ERROR: Simulation data not found at /mnt/mycephfs/gray-scott/gs-cephfs.bp"
        echo "Please run simulation first: $0 sim"
        exit 1
    fi
    
    # Create analysis directory
    mkdir -p /mnt/mycephfs/gray-scott/analysis
    
    # Run analysis
    echo "Starting analysis..."
    mpirun -n $processes ./build/adios2-pdf-calc \
        /mnt/mycephfs/gray-scott/gs-cephfs.bp \
        /mnt/mycephfs/gray-scott/analysis/pdf-results-$(date +%Y%m%d_%H%M%S).bp \
        $bins
    
    if [ $? -eq 0 ]; then
        echo "✅ Analysis completed successfully!"
        echo "Analysis results: /mnt/mycephfs/gray-scott/analysis/"
        ls -la /mnt/mycephfs/gray-scott/analysis/
    else
        echo "❌ Analysis failed!"
        exit 1
    fi
}

# Function to clean CephFS data
clean_data() {
    echo "Cleaning CephFS data..."
    rm -rf /mnt/mycephfs/gray-scott/*
    echo "✅ CephFS data cleaned"
}

# Function to show CephFS status
show_status() {
    echo "CephFS Status:"
    echo "Mount point: /mnt/mycephfs/"
    df -h /mnt/mycephfs/ 2>/dev/null || echo "❌ CephFS not mounted or not accessible"
    echo ""
    echo "Gray-Scott data on CephFS:"
    if [ -d "/mnt/mycephfs/gray-scott" ]; then
        ls -la /mnt/mycephfs/gray-scott/
        echo ""
        echo "Data usage:"
        du -sh /mnt/mycephfs/gray-scott/* 2>/dev/null || echo "No data found"
    else
        echo "No Gray-Scott data found on CephFS"
    fi
}

# Main command processing
case "$1" in
    "sim"|"simulation")
        run_simulation $2 $3
        ;;
    "analysis"|"analyze")
        run_analysis $2 $3
        ;;
    "clean")
        clean_data
        ;;
    "status")
        show_status
        ;;
    "help"|"--help"|"-h"|"")
        echo "Usage: $0 <command> [options]"
        echo ""
        echo "Commands:"
        echo "  sim [processes] [steps]     Run Gray-Scott simulation"
        echo "                              Default: 4 processes, 1000 steps"
        echo "  analysis [processes] [bins] Run PDF analysis"
        echo "                              Default: 4 processes, 100 bins"
        echo "  clean                       Clean all CephFS data"
        echo "  status                      Show CephFS and data status"
        echo "  help                        Show this help"
        echo ""
        echo "Examples:"
        echo "  $0 sim                      # Run with defaults (4 proc, 1000 steps)"
        echo "  $0 sim 8 2000              # Run with 8 processes, 2000 steps"
        echo "  $0 analysis                 # Run analysis with defaults"
        echo "  $0 analysis 8 200          # Run analysis with 8 processes, 200 bins"
        echo "  $0 status                   # Check CephFS status"
        ;;
    *)
        echo "Unknown command: $1"
        echo "Use '$0 help' for usage information"
        exit 1
        ;;
esac

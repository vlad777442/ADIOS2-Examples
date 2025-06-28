#!/bin/bash

# Performance testing script for Gray-Scott PDF analysis
# This script runs the PDF analysis with different configurations to measure performance

echo "========================================"
echo "Gray-Scott PDF Analysis Performance Tests"
echo "========================================"

# Check if simulation data exists
if [ ! -d "gs.bp" ]; then
    echo "Error: gs.bp simulation data not found. Please run the simulation first:"
    echo "mpirun -n 4 ./build/adios2-gray-scott settings.json"
    exit 1
fi

# Create output directory for performance tests
mkdir -p performance_results

echo ""
echo "Test 1: 2 processes, 50 bins, PDF only"
echo "----------------------------------------"
mpirun -n 2 ./build/adios2-pdf-calc gs.bp performance_results/test1_2p_50bins.bp 50

echo ""
echo "Test 2: 4 processes, 50 bins, PDF only"
echo "----------------------------------------"
mpirun -n 4 ./build/adios2-pdf-calc gs.bp performance_results/test2_4p_50bins.bp 50

echo ""
echo "Test 3: 2 processes, 100 bins, PDF only"
echo "-----------------------------------------"
mpirun -n 2 ./build/adios2-pdf-calc gs.bp performance_results/test3_2p_100bins.bp 100

echo ""
echo "Test 4: 4 processes, 100 bins, PDF only"
echo "-----------------------------------------"
mpirun -n 4 ./build/adios2-pdf-calc gs.bp performance_results/test4_4p_100bins.bp 100

echo ""
echo "Test 5: 4 processes, 100 bins, PDF + input data"
echo "------------------------------------------------"
mpirun -n 4 ./build/adios2-pdf-calc gs.bp performance_results/test5_4p_100bins_with_input.bp 100 YES

echo ""
echo "Test 6: 8 processes, 100 bins, PDF only"
echo "-----------------------------------------"
mpirun -n 8 ./build/adios2-pdf-calc gs.bp performance_results/test6_8p_100bins.bp 100

echo ""
echo "========================================"
echo "Performance tests completed!"
echo "Results saved in performance_results/"
echo "========================================"

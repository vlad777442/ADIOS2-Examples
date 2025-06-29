./run_cephfs.sh sim        # Run simulation
./run_cephfs.sh analysis   # Run analysis  
./run_cephfs.sh status     # Check status
./run_cephfs.sh clean      # Clean data


# Quick start
cd /users/vlad777/research/ADIOS2-Examples/source/cpp/gray-scott

# Run simulation on CephFS
./run_cephfs.sh sim 8 2000

# Analyze results
./run_cephfs.sh analysis 8 200

# Check status and performance
./run_cephfs.sh status


cd /users/vlad777/research/ADIOS2-Examples/source/cpp/gray-scott && mpirun -n 4 ./build/adios2-pdf-calc /mnt/mycephfs/gray-scott/gs-cephfs.bp /mnt/mycephfs/gray-scott/analysis/perf-with-writes.bp 50 YES

# Run with both read and write operations
mpirun -n 8 ./build/adios2-pdf-calc \
    /mnt/mycephfs/gray-scott/gs-cephfs.bp \
    /mnt/mycephfs/gray-scott/analysis/performance-test.bp \
    100 YES  # YES enables writing input data = WRITE performance

# Without the YES flag = only READ performance measured
mpirun -n 8 ./build/adios2-pdf-calc \
    /mnt/mycephfs/gray-scott/gs-cephfs.bp \
    /mnt/mycephfs/gray-scott/analysis/read-only-test.bp \
    100
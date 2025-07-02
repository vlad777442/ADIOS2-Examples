# Gray-Scott CephFS Performance Testing Guide

## Quick Start Commands

```bash
./run_cephfs.sh sim        # Run Gray-Scott simulation (writes BP data to CephFS)
./run_cephfs.sh analysis   # Run PDF analysis (reads BP data, writes analysis results)  
./run_cephfs.sh status     # Check CephFS data status and file sizes
./run_cephfs.sh clean      # Clean CephFS data directories
```

## Typical Workflow

```bash
cd /users/vlad777/research/ADIOS2-Examples/source/cpp/gray-scott

# 1. Run simulation on CephFS (writes timestep data)
./run_cephfs.sh sim 8 2000

# 2. Analyze results (reads data + writes PDF analysis)
./run_cephfs.sh analysis 8 200

# 3. Check status and performance
./run_cephfs.sh status
```

---

## Gray-Scott Workflow Overview

### 🔄 Two Main Programs:

| Program | Purpose | I/O Operations | Performance Metrics |
|---------|---------|----------------|-------------------|
| **`adios2-gray-scott`** | Simulation | **WRITES** timestep data (U,V fields) to CephFS | ✅ Write throughput, data volume, timing breakdown |
| **`adios2-pdf-calc`** | Analysis | **READS** simulation data + **WRITES** PDF results | ✅ Read/write throughput, computation time |

### 📊 Data Flow:
```
┌─────────────┐    ┌──────────┐    ┌──────────┐    ┌─────────────┐
│ Simulation  │───▶│ BP Files │───▶│ Analysis │───▶│ PDF Results │
│   (write)   │    │ (CephFS) │    │(read+write)│   │   (output)  │
└─────────────┘    └──────────┘    └──────────┘    └─────────────┘
```

---

## 🧪 Performance Testing Commands

### 1. Simulation Performance (Write-Heavy)
```bash
cd ~/research/ADIOS2-Examples/source/cpp/gray-scott
mpirun -n 4 ./build/adios2-gray-scott settings-ceph.json
```
- **Tests**: CephFS write performance during simulation
- **Measures**: Write throughput, I/O vs computation time, data volume
- **Output**: Comprehensive performance summary at the end

### 2. Analysis Performance Options

#### High Write Volume Test
```bash
mpirun -n 8 ./build/adios2-pdf-calc \
    /mnt/mycephfs/gray-scott/gs-cephfs.bp \
    /mnt/mycephfs/gray-scott/analysis/performance-test.bp \
    100 YES
```
- **`YES` flag**: Writes original U,V data + PDF results
- **Tests**: High-volume read + write performance  
- **Use case**: Stress testing CephFS with large data transfers

#### Low Write Volume Test  
```bash
mpirun -n 8 ./build/adios2-pdf-calc \
    /mnt/mycephfs/gray-scott/gs-cephfs.bp \
    /mnt/mycephfs/gray-scott/analysis/read-only-test.bp \
    100
```
- **No `YES`**: Writes only PDF analysis results
- **Tests**: Normal read + minimal write performance
- **Use case**: Typical analysis workflow performance

---

## 📈 Performance Metrics Explained

Both programs now provide detailed performance breakdowns:

- **⏱️ Timing**: Initialization, computation, I/O read, I/O write  
- **💾 Data Volume**: Total GB read/written, per-operation sizes
- **🚀 Throughput**: GB/s for read and write operations
- **📊 Breakdown**: Percentage of time spent in each operation
- **🔧 MPI Info**: Process count, domain decomposition details

This setup allows comprehensive testing of CephFS performance under different I/O patterns and data loads!
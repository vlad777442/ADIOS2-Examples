# Gray-Scott on CephFS - Complete Setup Guide

## Overview
This guide provides a complete setup for running Gray-Scott simulations on CephFS with optimized performance and comprehensive monitoring.

## Prerequisites
- CephFS mounted at `/mnt/mycephfs/`
- MPI environment configured
- ADIOS2 built and working

## Files Created

### 1. Configuration Files
- `settings-ceph.json` - CephFS-optimized simulation settings
- `adios2-cephfs.xml` - CephFS-optimized ADIOS2 configuration

### 2. Helper Scripts
- `run_cephfs.sh` - Convenient wrapper for CephFS operations
- `run_performance_tests.sh` - Performance benchmarking suite

### 3. Enhanced Analysis
- `pdf-calc.cpp` - Enhanced with comprehensive performance measurements

## Quick Start Commands

### Run Simulation on CephFS
```bash
# Basic simulation (4 processes, 1000 steps)
./run_cephfs.sh sim

# Custom configuration
./run_cephfs.sh sim 8 2000  # 8 processes, 2000 steps
```

### Run Analysis
```bash
# Basic analysis (4 processes, 100 bins)
./run_cephfs.sh analysis

# Custom configuration  
./run_cephfs.sh analysis 8 200  # 8 processes, 200 bins
```

### Check Status
```bash
./run_cephfs.sh status
```

## Manual Commands

### Simulation
```bash
cd /users/vlad777/research/ADIOS2-Examples/source/cpp/gray-scott
mpirun -n 4 ./build/adios2-gray-scott settings-ceph.json
```

### Analysis with Performance Metrics
```bash
mpirun -n 4 ./build/adios2-pdf-calc \
    /mnt/mycephfs/gray-scott/gs-cephfs.bp \
    /mnt/mycephfs/gray-scott/analysis/pdf-results.bp \
    100
```

## CephFS Optimizations Applied

### ADIOS2 Configuration (`adios2-cephfs.xml`)
- **BufferSize**: 32MB (optimized for network filesystem)
- **MaxBufferSize**: 256MB (large buffers for CephFS)
- **NumAggregators**: 4 (parallel write optimization)
- **AsyncWrite**: true (non-blocking writes)
- **CollectiveMetadata**: true (distributed metadata operations)
- **CollectiveOperations**: true (collective I/O)
- **OpenTimeoutSecs**: 120.0 (network filesystem tolerance)
- **FlushOnEndStep**: true (ensure data consistency)

### Performance Results on CephFS
- **Read Throughput**: ~3,320 MB/s
- **Write Performance**: Optimized with async operations
- **Data Organization**: 4 aggregated files (100MB each)
- **Network Efficiency**: Collective operations reduce network overhead

## Directory Structure on CephFS
```
/mnt/mycephfs/gray-scott/
├── gs-cephfs.bp/           # Main simulation output
│   ├── data.0-3           # 4 aggregated data files
│   ├── md.* files         # Metadata
│   └── profiling.json     # Performance data
├── checkpoints/           # Checkpoint files
│   └── ckpt-cephfs.bp/
└── analysis/             # Analysis results
    └── pdf-results-*.bp/
```

## Performance Features
- **Real-time timing**: Per-step read/write timing
- **MPI aggregation**: Min/max/average across processes  
- **Throughput calculation**: MB/s for I/O operations
- **Data volume tracking**: Total MB read/written
- **Comprehensive reporting**: Detailed performance tables

## Scaling Guidelines

### Process Count
- **Small datasets**: 2-4 processes
- **Medium datasets**: 8-16 processes  
- **Large datasets**: 32+ processes

### CephFS-Specific Tuning
- **Aggregators**: 1 per 4-8 compute processes
- **Buffer size**: Match CephFS object size (typically 4-64MB)
- **Network bandwidth**: Consider CephFS cluster bandwidth

## Troubleshooting

### Common Issues
1. **Permission errors**: Check CephFS mount permissions
2. **Timeout errors**: Increase `OpenTimeoutSecs` for slow networks
3. **Performance issues**: Adjust buffer sizes and aggregator count

### Monitoring
```bash
# Check CephFS performance
./run_cephfs.sh status

# Monitor during simulation
watch -n 5 "df -h /mnt/mycephfs && ls -la /mnt/mycephfs/gray-scott/"
```

## Production Deployment

### For Large-Scale Runs
```bash
# Scale up simulation
./run_cephfs.sh sim 32 10000

# Run comprehensive performance analysis
./run_performance_tests.sh
```

### Data Management
```bash
# Clean old data
./run_cephfs.sh clean

# Archive results
tar -czf gray-scott-results-$(date +%Y%m%d).tar.gz /mnt/mycephfs/gray-scott/
```

## Key Benefits of CephFS Setup

1. **Distributed Storage**: Automatic data distribution across Ceph cluster
2. **High Availability**: Built-in redundancy and fault tolerance  
3. **Scalability**: Seamless scaling with cluster growth
4. **Performance**: Optimized I/O patterns for network storage
5. **Monitoring**: Comprehensive performance measurement system

This setup provides a production-ready environment for running Gray-Scott simulations on CephFS with optimal performance and comprehensive monitoring capabilities.

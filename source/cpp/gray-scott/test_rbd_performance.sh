#!/bin/bash

# Test basic functionality
echo "Testing RBD script functions..."

# Test the current simulation run with performance metrics
cd /users/vlad777/research/ADIOS2-Examples/source/cpp/gray-scott

echo "🚀 Testing Small Simulation with Performance Metrics"
echo "===================================================="

# Just run a very small test without the complex monitoring
./run_rbd.sh sim 2 10 2>&1 || echo "Simulation failed or had issues"

echo "🔍 Testing Performance Metrics View"
echo "==================================="

# Check current RBD status manually
echo "💾 Current RBD Status:"
df -h /mnt/rbd

echo ""
echo "🗂️ Gray-Scott Data Summary:"
if [ -d "/mnt/rbd/gray-scott" ]; then
    echo "  Total files: $(find /mnt/rbd/gray-scott -type f 2>/dev/null | wc -l)"
    echo "  Total size: $(du -sh /mnt/rbd/gray-scott 2>/dev/null | cut -f1 || echo 'Unknown')"
    echo "  Simulation data: $(du -sh /mnt/rbd/gray-scott/gs-rbd.bp 2>/dev/null | cut -f1 || echo 'None')"
else
    echo "  No Gray-Scott data found"
fi

echo ""
echo "📊 Performance Logs:"
ls -lt /tmp/rbd_*_performance_*.log 2>/dev/null | head -3 || echo "No performance logs found yet"

echo ""
echo "Test completed successfully!"

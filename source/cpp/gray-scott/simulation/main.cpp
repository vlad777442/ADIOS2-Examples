#include <fstream>
#include <iostream>
#include <sstream>
#include <vector>
#include <chrono>
#include <iomanip>

#include <adios2.h>
#include <mpi.h>

#include "../../gray-scott/common/timer.hpp"
#include "../../gray-scott/simulation/gray-scott.h"
#include "../../gray-scott/simulation/restart.h"
#include "../../gray-scott/simulation/writer.h"

// Performance metrics structure
struct SimulationPerformanceMetrics {
    double io_write_time = 0.0;
    double io_checkpoint_time = 0.0;
    double computation_time = 0.0;
    double initialization_time = 0.0;
    double total_time = 0.0;
    double data_size_gb = 0.0;
    double checkpoint_size_gb = 0.0;
    int total_writes = 0;
    int total_checkpoints = 0;
    
    // Per-step timing
    std::vector<double> step_write_times;
    std::vector<double> step_compute_times;
    std::vector<double> step_data_sizes_mb;
};

void print_io_settings(const adios2::IO &io)
{
    std::cout << "Simulation writes data using engine type:              "
              << io.EngineType() << std::endl;
    auto ioparams = io.Parameters();
    std::cout << "IO parameters:  " << std::endl;
    for (const auto &p : ioparams)
    {
        std::cout << "    " << p.first << " = " << p.second << std::endl;
    }
}

void print_settings(const Settings &s, int restart_step)
{
    std::cout << "grid:             " << s.L << "x" << s.L << "x" << s.L
              << std::endl;
    if (restart_step > 0)
    {
        std::cout << "restart:          from step " << restart_step
                  << std::endl;
    }
    else
    {
        std::cout << "restart:          no" << std::endl;
    }
    std::cout << "steps:            " << s.steps << std::endl;
    std::cout << "plotgap:          " << s.plotgap << std::endl;
    std::cout << "F:                " << s.F << std::endl;
    std::cout << "k:                " << s.k << std::endl;
    std::cout << "dt:               " << s.dt << std::endl;
    std::cout << "Du:               " << s.Du << std::endl;
    std::cout << "Dv:               " << s.Dv << std::endl;
    std::cout << "noise:            " << s.noise << std::endl;
    std::cout << "output:           " << s.output << std::endl;
    std::cout << "adios_config:     " << s.adios_config << std::endl;
}

void print_simulator_settings(const GrayScott &s)
{
    std::cout << "process layout:   " << s.npx << "x" << s.npy << "x" << s.npz
              << std::endl;
    std::cout << "local grid size:  " << s.size_x << "x" << s.size_y << "x"
              << s.size_z << std::endl;
}

void print_performance_summary(const SimulationPerformanceMetrics &metrics, int rank, int comm_size, const Settings &settings)
{
    if (rank == 0)
    {
        std::cout << "\n========================================"
                  << "\nGRAY-SCOTT SIMULATION PERFORMANCE SUMMARY"
                  << "\n========================================"
                  << std::fixed << std::setprecision(4)
                  << "\nTotal execution time:     " << metrics.total_time << " seconds"
                  << "\nInitialization time:      " << metrics.initialization_time << " seconds"
                  << "\nComputation time:         " << metrics.computation_time << " seconds"
                  << "\nI/O write time:           " << metrics.io_write_time << " seconds"
                  << "\nCheckpoint time:          " << metrics.io_checkpoint_time << " seconds"
                  << "\n"
                  << "\nData output statistics:"
                  << "\n  Total writes:           " << metrics.total_writes
                  << "\n  Total data written:     " << metrics.data_size_gb << " GB"
                  << "\n  Write throughput:       " << (metrics.data_size_gb / metrics.io_write_time) << " GB/s"
                  << "\n  Average per write:      " << (metrics.data_size_gb * 1024.0 / metrics.total_writes) << " MB"
                  << "\n"
                  << "\nCheckpoint statistics:"
                  << "\n  Total checkpoints:      " << metrics.total_checkpoints
                  << "\n  Checkpoint data:        " << metrics.checkpoint_size_gb << " GB"
                  << std::setprecision(2)
                  << "\n"
                  << "\nPerformance breakdown:"
                  << "\n  Computation:            " << (metrics.computation_time / metrics.total_time * 100) << "%"
                  << "\n  I/O write:              " << (metrics.io_write_time / metrics.total_time * 100) << "%"
                  << "\n  Checkpoint:             " << (metrics.io_checkpoint_time / metrics.total_time * 100) << "%"
                  << "\n  Other:                  " << ((metrics.total_time - metrics.computation_time - metrics.io_write_time - metrics.io_checkpoint_time) / metrics.total_time * 100) << "%"
                  << std::setprecision(4)
                  << "\n"
                  << "\nMPI Configuration:"
                  << "\n  Processes:              " << comm_size
                  << "\n  Grid decomposition:     " << settings.L << "x" << settings.L << "x" << settings.L
                  << "\n  Steps simulated:        " << settings.steps
                  << "\n  Plot gap:               " << settings.plotgap
                  << "\n========================================"
                  << std::endl;
    }
}

double calculate_data_size_mb(const GrayScott &sim)
{
    // Calculate size in MB for U + V + step data
    size_t u_size = sim.size_x * sim.size_y * sim.size_z * sizeof(double);
    size_t v_size = sim.size_x * sim.size_y * sim.size_z * sizeof(double);
    size_t step_size = sizeof(int);
    return (u_size + v_size + step_size) / (1024.0 * 1024.0);
}

int main(int argc, char **argv)
{
    // Start overall timing
    auto start_total = std::chrono::high_resolution_clock::now();
    
    int provided;
    MPI_Init_thread(&argc, &argv, MPI_THREAD_MULTIPLE, &provided);
    int rank, procs, wrank;

    MPI_Comm_rank(MPI_COMM_WORLD, &wrank);

    const unsigned int color = 1;
    MPI_Comm comm;
    MPI_Comm_split(MPI_COMM_WORLD, color, wrank, &comm);

    MPI_Comm_rank(comm, &rank);
    MPI_Comm_size(comm, &procs);

    // Initialize performance metrics
    SimulationPerformanceMetrics perf_metrics;

    if (argc < 2)
    {
        if (rank == 0)
        {
            std::cerr << "Too few arguments" << std::endl;
            std::cerr << "Usage: gray-scott settings.json" << std::endl;
        }
        MPI_Abort(MPI_COMM_WORLD, -1);
    }

    // Start initialization timing
    auto start_init = std::chrono::high_resolution_clock::now();

    Settings settings = Settings::from_json(argv[1]);

    GrayScott sim(settings, comm);
    sim.init();

    adios2::ADIOS adios(settings.adios_config, comm);
    adios2::IO io_main = adios.DeclareIO("SimulationOutput");
    adios2::IO io_ckpt = adios.DeclareIO("SimulationCheckpoint");

    int restart_step = 0;
    if (settings.restart)
    {
        restart_step = ReadRestart(comm, settings, sim, io_ckpt);
        io_main.SetParameter("AppendAfterSteps",
                             std::to_string(restart_step / settings.plotgap));
    }

    Writer writer_main(settings, sim, io_main);
    writer_main.open(settings.output, (restart_step > 0));

    // End initialization timing
    auto end_init = std::chrono::high_resolution_clock::now();
    perf_metrics.initialization_time = std::chrono::duration<double>(end_init - start_init).count();

    if (rank == 0)
    {
        print_io_settings(io_main);
        std::cout << "========================================" << std::endl;
        print_settings(settings, restart_step);
        print_simulator_settings(sim);
        std::cout << "========================================" << std::endl;
    }

#ifdef ENABLE_TIMERS
    Timer timer_total;
    Timer timer_compute;
    Timer timer_write;

    std::ostringstream log_fname;
    log_fname << "gray_scott_pe_" << rank << ".log";

    std::ofstream log(log_fname.str());
    log << "step\ttotal_gs\tcompute_gs\twrite_gs" << std::endl;
#endif

    for (int it = restart_step; it < settings.steps;)
    {
#ifdef ENABLE_TIMERS
        MPI_Barrier(comm);
        timer_total.start();
        timer_compute.start();
#endif

        // Start computation timing
        auto start_compute = std::chrono::high_resolution_clock::now();

        sim.iterate();
        it++;

        // End computation timing
        auto end_compute = std::chrono::high_resolution_clock::now();
        double compute_time = std::chrono::duration<double>(end_compute - start_compute).count();
        perf_metrics.computation_time += compute_time;
        perf_metrics.step_compute_times.push_back(compute_time);

#ifdef ENABLE_TIMERS
        timer_compute.stop();
        MPI_Barrier(comm);
        timer_write.start();
#endif

        if (it % settings.plotgap == 0)
        {
            if (rank == 0)
            {
                std::cout << "Simulation at step " << it
                          << " writing output step     "
                          << it / settings.plotgap << std::endl;
            }

            // Start I/O write timing
            auto start_write = std::chrono::high_resolution_clock::now();
            
            writer_main.write(it, sim);
            
            // End I/O write timing and calculate data size
            auto end_write = std::chrono::high_resolution_clock::now();
            double write_time = std::chrono::duration<double>(end_write - start_write).count();
            perf_metrics.io_write_time += write_time;
            perf_metrics.step_write_times.push_back(write_time);
            
            // Calculate data size for this write
            double data_size_mb = calculate_data_size_mb(sim);
            perf_metrics.step_data_sizes_mb.push_back(data_size_mb);
            perf_metrics.data_size_gb += data_size_mb / 1024.0;
            perf_metrics.total_writes++;
        }

        if (settings.checkpoint && (it % settings.checkpoint_freq) == 0)
        {
            // Start checkpoint timing
            auto start_checkpoint = std::chrono::high_resolution_clock::now();
            
            WriteCkpt(comm, it, settings, sim, io_ckpt);
            
            // End checkpoint timing
            auto end_checkpoint = std::chrono::high_resolution_clock::now();
            double checkpoint_time = std::chrono::duration<double>(end_checkpoint - start_checkpoint).count();
            perf_metrics.io_checkpoint_time += checkpoint_time;
            
            // Estimate checkpoint size (full U + V arrays with ghosts)
            size_t full_array_size = (sim.size_x + 2) * (sim.size_y + 2) * (sim.size_z + 2) * sizeof(double);
            double checkpoint_size_mb = (2 * full_array_size + sizeof(int)) / (1024.0 * 1024.0);
            perf_metrics.checkpoint_size_gb += checkpoint_size_mb / 1024.0;
            perf_metrics.total_checkpoints++;
        }

#ifdef ENABLE_TIMERS
        double time_write = timer_write.stop();
        double time_step = timer_total.stop();
        MPI_Barrier(comm);

        log << it << "\t" << timer_total.elapsed() << "\t"
            << timer_compute.elapsed() << "\t" << timer_write.elapsed()
            << std::endl;
#endif
    }

    writer_main.close();

    // Calculate total execution time
    auto end_total = std::chrono::high_resolution_clock::now();
    perf_metrics.total_time = std::chrono::duration<double>(end_total - start_total).count();

    // Aggregate performance metrics across all processes
    double total_write_time_all = 0.0;
    double total_checkpoint_time_all = 0.0;
    double total_compute_time_all = 0.0;
    double total_data_gb_all = 0.0;
    double total_checkpoint_gb_all = 0.0;
    int total_writes_all = 0;
    int total_checkpoints_all = 0;

    MPI_Reduce(&perf_metrics.io_write_time, &total_write_time_all, 1, MPI_DOUBLE, MPI_SUM, 0, comm);
    MPI_Reduce(&perf_metrics.io_checkpoint_time, &total_checkpoint_time_all, 1, MPI_DOUBLE, MPI_SUM, 0, comm);
    MPI_Reduce(&perf_metrics.computation_time, &total_compute_time_all, 1, MPI_DOUBLE, MPI_SUM, 0, comm);
    MPI_Reduce(&perf_metrics.data_size_gb, &total_data_gb_all, 1, MPI_DOUBLE, MPI_SUM, 0, comm);
    MPI_Reduce(&perf_metrics.checkpoint_size_gb, &total_checkpoint_gb_all, 1, MPI_DOUBLE, MPI_SUM, 0, comm);
    MPI_Reduce(&perf_metrics.total_writes, &total_writes_all, 1, MPI_INT, MPI_SUM, 0, comm);
    MPI_Reduce(&perf_metrics.total_checkpoints, &total_checkpoints_all, 1, MPI_INT, MPI_SUM, 0, comm);

    // Update metrics with aggregated values for rank 0
    if (rank == 0)
    {
        // Use average times across processes for meaningful metrics
        perf_metrics.io_write_time = total_write_time_all / procs;
        perf_metrics.io_checkpoint_time = total_checkpoint_time_all / procs;
        perf_metrics.computation_time = total_compute_time_all / procs;
        perf_metrics.data_size_gb = total_data_gb_all;
        perf_metrics.checkpoint_size_gb = total_checkpoint_gb_all;
        perf_metrics.total_writes = total_writes_all / procs;  // Average writes per process
        perf_metrics.total_checkpoints = total_checkpoints_all / procs;
    }

    // Print performance summary
    print_performance_summary(perf_metrics, rank, procs, settings);

    // Output per-step throughput CSV for plotting
    if (rank == 0 && !perf_metrics.step_write_times.empty())
    {
        std::string csv_filename = settings.output + "_throughput.csv";
        std::ofstream csv_file(csv_filename);
        if (csv_file.is_open())
        {
            csv_file << "write_number,step,write_time_sec,data_size_mb,throughput_mb_s,cumulative_time_sec,cumulative_data_mb\n";
            
            double cumulative_time = 0.0;
            double cumulative_data = 0.0;
            
            for (size_t i = 0; i < perf_metrics.step_write_times.size(); ++i)
            {
                double write_time = perf_metrics.step_write_times[i];
                double data_size_mb = perf_metrics.step_data_sizes_mb[i];
                double throughput = (write_time > 0) ? (data_size_mb / write_time) : 0.0;
                
                cumulative_time += write_time;
                cumulative_data += data_size_mb;
                
                int step_num = (i + 1) * settings.plotgap;
                
                csv_file << std::fixed << std::setprecision(6)
                         << (i + 1) << ","
                         << step_num << ","
                         << write_time << ","
                         << data_size_mb << ","
                         << throughput << ","
                         << cumulative_time << ","
                         << cumulative_data << "\n";
            }
            csv_file.close();
            std::cout << "\n📊 Per-step throughput data saved to: " << csv_filename << std::endl;
        }
    }

#ifdef ENABLE_TIMERS
    log << "total\t" << timer_total.elapsed() << "\t" << timer_compute.elapsed()
        << "\t" << timer_write.elapsed() << std::endl;

    log.close();
#endif

    MPI_Finalize();
}

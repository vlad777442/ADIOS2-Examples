/*
 * Analysis code for the Gray-Scott application.
 * Reads variable U and V, and computes the PDF for each 2D slices of U and V.
 * Writes the computed PDFs using ADIOS.
 *
 * Norbert Podhorszki, pnorbert@ornl.gov
 *
 */
#include <mpi.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <iomanip>

#include "adios2.h"

// Performance measurement structure
struct PerformanceMetrics {
    double total_time = 0.0;
    double io_read_time = 0.0;
    double io_write_time = 0.0;
    double computation_time = 0.0;
    double initialization_time = 0.0;
    size_t total_steps = 0;
    size_t total_data_read_mb = 0;
    size_t total_data_written_mb = 0;
    
    void print_summary(int rank, int comm_size) {
        if (rank == 0) {
            std::cout << "\n=== Performance Summary ===" << std::endl;
            std::cout << std::fixed << std::setprecision(3);
            std::cout << "Total execution time:     " << total_time << " seconds" << std::endl;
            std::cout << "Initialization time:      " << initialization_time << " seconds" << std::endl;
            std::cout << "I/O read time:            " << io_read_time << " seconds" << std::endl;
            std::cout << "Computation time:         " << computation_time << " seconds" << std::endl;
            std::cout << "I/O write time:           " << io_write_time << " seconds" << std::endl;
            std::cout << "Total steps processed:    " << total_steps << std::endl;
            std::cout << "Data read (MB):           " << total_data_read_mb << std::endl;
            std::cout << "Data written (MB):        " << total_data_written_mb << std::endl;
            std::cout << "Processes used:           " << comm_size << std::endl;
            
            if (total_steps > 0) {
                std::cout << "Average time per step:    " << (total_time - initialization_time) / total_steps << " seconds" << std::endl;
                std::cout << "Read throughput:          " << (total_data_read_mb / io_read_time) << " MB/s" << std::endl;
                std::cout << "Write throughput:         " << (total_data_written_mb / io_write_time) << " MB/s" << std::endl;
            }
            std::cout << "===========================\n" << std::endl;
        }
    }
};

bool epsilon(double d) { return (d < 1.0e-20); }
bool epsilon(float d) { return (d < 1.0e-20); }

/*
 * Function to compute the PDF of a 2D slice
 */
template <class T>
void compute_pdf(const std::vector<T> &data,
                 const std::vector<std::size_t> &shape, const size_t start,
                 const size_t count, const size_t nbins, const T min,
                 const T max, std::vector<T> &pdf, std::vector<T> &bins)
{
    if (shape.size() != 3)
        throw std::invalid_argument("ERROR: shape is expected to be 3D\n");

    size_t slice_size = shape[1] * shape[2];
    pdf.resize(count * nbins);
    bins.resize(nbins);

    size_t start_data = 0;
    size_t start_pdf = 0;

    T binWidth = (max - min) / nbins;
    for (auto i = 0; i < nbins; ++i)
    {
        bins[i] = min + (i * binWidth);
    }

    if (nbins == 1)
    {
        // special case: only one bin
        for (auto i = 0; i < count; ++i)
        {
            pdf[i] = slice_size;
        }
        return;
    }

    if (epsilon(max - min) || epsilon(binWidth))
    {
        // special case: constant array
        for (auto i = 0; i < count; ++i)
        {
            pdf[i * nbins + (nbins / 2)] = slice_size;
        }
        return;
    }

    for (auto i = 0; i < count; ++i)
    {
        // Calculate a PDF for 'nbins' bins for values between 'min' and 'max'
        // from data[ start_data .. start_data+slice_size-1 ]
        // into pdf[ start_pdf .. start_pdf+nbins-1 ]
        for (auto j = 0; j < slice_size; ++j)
        {
            if (data[start_data + j] > max || data[start_data + j] < min)
            {
                std::cout << " data[" << start * slice_size + start_data + j
                          << "] = " << data[start_data + j]
                          << " is out of [min,max] = [" << min << "," << max
                          << "]" << std::endl;
            }
            size_t bin = static_cast<size_t>(
                std::floor((data[start_data + j] - min) / binWidth));
            if (bin == nbins)
            {
                bin = nbins - 1;
            }
            ++pdf[start_pdf + bin];
        }
        start_pdf += nbins;
        start_data += slice_size;
    }
    return;
}

/*
 * Print info to the user on how to invoke the application
 */
void printUsage()
{
    std::cout
        << "Usage: pdf_calc input output [N] [output_inputdata]\n"
        << "  input:   Name of the input file handle for reading data\n"
        << "  output:  Name of the output file to which data must be written\n"
        << "  N:       Number of bins for the PDF calculation, default = 1000\n"
        << "  output_inputdata: YES will write the original variables besides "
           "the analysis results\n\n";
}

/*
 * MAIN
 */
int main(int argc, char *argv[])
{
    // Start overall timing
    auto start_total = std::chrono::high_resolution_clock::now();
    
    int provided;
    MPI_Init_thread(&argc, &argv, MPI_THREAD_MULTIPLE, &provided);
    int rank, comm_size, wrank;

    MPI_Comm_rank(MPI_COMM_WORLD, &wrank);

    const unsigned int color = 2;
    MPI_Comm comm;
    MPI_Comm_split(MPI_COMM_WORLD, color, wrank, &comm);

    MPI_Comm_rank(comm, &rank);
    MPI_Comm_size(comm, &comm_size);
    
    // Initialize performance metrics
    PerformanceMetrics perf_metrics;

    if (argc < 3)
    {
        std::cout << "Not enough arguments\n";
        if (rank == 0)
            printUsage();
        MPI_Finalize();
        return 0;
    }

    std::string in_filename;
    std::string out_filename;
    size_t nbins = 1000;
    bool write_inputvars = false;
    in_filename = argv[1];
    out_filename = argv[2];

    if (argc >= 4)
    {
        int value = std::stoi(argv[3]);
        if (value > 0)
            nbins = static_cast<size_t>(value);
    }

    if (argc >= 5)
    {
        std::string value = argv[4];
        std::transform(value.begin(), value.end(), value.begin(), ::tolower);
        if (value == "yes")
            write_inputvars = true;
    }

    std::size_t u_global_size, v_global_size;
    std::size_t u_local_size, v_local_size;

    bool firstStep = true;

    std::vector<std::size_t> shape;

    std::vector<double> u;
    std::vector<double> v;
    int simStep = -5;

    std::vector<double> pdf_u;
    std::vector<double> pdf_v;
    std::vector<double> bins_u;
    std::vector<double> bins_v;

    // adios2 variable declarations
    adios2::Variable<double> var_u_in, var_v_in;
    adios2::Variable<int> var_step_in;
    adios2::Variable<double> var_u_pdf, var_v_pdf;
    adios2::Variable<double> var_u_bins, var_v_bins;
    adios2::Variable<int> var_step_out;
    adios2::Variable<double> var_u_out, var_v_out;

    {
        // Start initialization timing
        auto start_init = std::chrono::high_resolution_clock::now();
        
        // adios2 io object and engine init
        adios2::ADIOS ad("adios2.xml", comm);

        // IO objects for reading and writing
        adios2::IO reader_io = ad.DeclareIO("SimulationOutput");
        adios2::IO writer_io = ad.DeclareIO("PDFAnalysisOutput");
        if (!rank)
        {
            std::cout
                << "PDF analysis reads from Simulation using engine type:  "
                << reader_io.EngineType() << std::endl;
            std::cout
                << "PDF analysis writes using engine type:                 "
                << writer_io.EngineType() << std::endl;
        }

        // Engines for reading and writing
        adios2::Engine reader =
            reader_io.Open(in_filename, adios2::Mode::Read, comm);
        adios2::Engine writer =
            writer_io.Open(out_filename, adios2::Mode::Write, comm);

        bool shouldIWrite = (!rank || reader_io.EngineType() == "HDF5");
        
        // End initialization timing
        auto end_init = std::chrono::high_resolution_clock::now();
        perf_metrics.initialization_time = std::chrono::duration<double>(end_init - start_init).count();

        // read data per timestep
        int stepAnalysis = 0;
        while (true)
        {
            // Start I/O read timing
            auto start_read = std::chrono::high_resolution_clock::now();

            // Begin step
            adios2::StepStatus read_status =
                reader.BeginStep(adios2::StepMode::Read, 10.0f);
            if (read_status == adios2::StepStatus::NotReady)
            {
                // std::cout << "Stream not ready yet. Waiting...\n";
                std::this_thread::sleep_for(std::chrono::milliseconds(1000));
                continue;
            }
            else if (read_status != adios2::StepStatus::OK)
            {
                break;
            }

            int stepSimOut = reader.CurrentStep();

            // Inquire variable and set the selection at the first step only
            // This assumes that the variable dimensions do not change across
            // timesteps

            // Inquire variable
            var_u_in = reader_io.InquireVariable<double>("U");
            var_v_in = reader_io.InquireVariable<double>("V");
            var_step_in = reader_io.InquireVariable<int>("step");

            std::pair<double, double> minmax_u = var_u_in.MinMax();
            std::pair<double, double> minmax_v = var_v_in.MinMax();

            shape = var_u_in.Shape();

            // Calculate global and local sizes of U and V
            u_global_size = shape[0] * shape[1] * shape[2];
            u_local_size = u_global_size / comm_size;
            v_global_size = shape[0] * shape[1] * shape[2];
            v_local_size = v_global_size / comm_size;

            size_t count1 = shape[0] / comm_size;
            size_t start1 = count1 * rank;
            if (rank == comm_size - 1)
            {
                // last process need to read all the rest of slices
                count1 = shape[0] - count1 * (comm_size - 1);
            }

            /*std::cout << "  rank " << rank << " slice start={" <<  start1
              << ",0,0} count={" << count1  << "," << shape[1] << "," <<
              shape[2]
              << "}" << std::endl;*/

            // Set selection
            var_u_in.SetSelection(adios2::Box<adios2::Dims>(
                {start1, 0, 0}, {count1, shape[1], shape[2]}));
            var_v_in.SetSelection(adios2::Box<adios2::Dims>(
                {start1, 0, 0}, {count1, shape[1], shape[2]}));

            // Declare variables to output
            if (firstStep)
            {
                var_u_pdf = writer_io.DefineVariable<double>(
                    "U/pdf", {shape[0], nbins}, {start1, 0}, {count1, nbins});
                var_v_pdf = writer_io.DefineVariable<double>(
                    "V/pdf", {shape[0], nbins}, {start1, 0}, {count1, nbins});

                if (shouldIWrite)
                {
                    var_u_bins = writer_io.DefineVariable<double>(
                        "U/bins", {nbins}, {0}, {nbins});
                    var_v_bins = writer_io.DefineVariable<double>(
                        "V/bins", {nbins}, {0}, {nbins});
                    var_step_out = writer_io.DefineVariable<int>("step");
                }

                if (write_inputvars)
                {
                    var_u_out = writer_io.DefineVariable<double>(
                        "U", {shape[0], shape[1], shape[2]}, {start1, 0, 0},
                        {count1, shape[1], shape[2]});
                    var_v_out = writer_io.DefineVariable<double>(
                        "V", {shape[0], shape[1], shape[2]}, {start1, 0, 0},
                        {count1, shape[1], shape[2]});
                }
                firstStep = false;
            }

            // Read adios2 data
            reader.Get<double>(var_u_in, u);
            reader.Get<double>(var_v_in, v);
            if (shouldIWrite)
            {
                reader.Get<int>(var_step_in, &simStep);
            }

            // End adios2 step
            reader.EndStep();
            
            // End I/O read timing and calculate data size
            auto end_read = std::chrono::high_resolution_clock::now();
            double read_time = std::chrono::duration<double>(end_read - start_read).count();
            perf_metrics.io_read_time += read_time;
            
            // Calculate data size read (U + V arrays)
            size_t data_size_bytes = (u.size() + v.size()) * sizeof(double);
            perf_metrics.total_data_read_mb += data_size_bytes / (1024 * 1024);

            if (!rank)
            {
                std::cout << "PDF Analysis step " << stepAnalysis
                          << " processing sim output step " << stepSimOut
                          << " sim compute step " << simStep 
                          << " (read time: " << std::fixed << std::setprecision(3) << read_time << "s)"
                          << std::endl;
            }

            // HDF5 engine does not provide min/max. Let's calculate it
            //        if (reader_io.EngineType() == "HDF5")
            {
                auto mmu = std::minmax_element(u.begin(), u.end());
                minmax_u = std::make_pair(*mmu.first, *mmu.second);
                auto mmv = std::minmax_element(v.begin(), v.end());
                minmax_v = std::make_pair(*mmv.first, *mmv.second);
            }

            // Start computation timing
            auto start_compute = std::chrono::high_resolution_clock::now();
            
            // Compute PDF
            std::vector<double> pdf_u;
            std::vector<double> bins_u;
            compute_pdf(u, shape, start1, count1, nbins, minmax_u.first,
                        minmax_u.second, pdf_u, bins_u);

            std::vector<double> pdf_v;
            std::vector<double> bins_v;
            compute_pdf(v, shape, start1, count1, nbins, minmax_v.first,
                        minmax_v.second, pdf_v, bins_v);
            
            // End computation timing
            auto end_compute = std::chrono::high_resolution_clock::now();
            perf_metrics.computation_time += std::chrono::duration<double>(end_compute - start_compute).count();

            // Start I/O write timing
            auto start_write = std::chrono::high_resolution_clock::now();

            // write U, V, and their norms out
            writer.BeginStep();
            writer.Put<double>(var_u_pdf, pdf_u.data());
            writer.Put<double>(var_v_pdf, pdf_v.data());
            if (shouldIWrite)
            {
                writer.Put<double>(var_u_bins, bins_u.data());
                writer.Put<double>(var_v_bins, bins_v.data());
                writer.Put<int>(var_step_out, simStep);
            }
            if (write_inputvars)
            {
                writer.Put<double>(var_u_out, u.data());
                writer.Put<double>(var_v_out, v.data());
            }
            writer.EndStep();
            
            // End I/O write timing and calculate data size written
            auto end_write = std::chrono::high_resolution_clock::now();
            perf_metrics.io_write_time += std::chrono::duration<double>(end_write - start_write).count();
            
            // Calculate data size written (PDF data + bins + optional input data)
            size_t write_size_bytes = (pdf_u.size() + pdf_v.size()) * sizeof(double);
            if (shouldIWrite) {
                write_size_bytes += (bins_u.size() + bins_v.size()) * sizeof(double) + sizeof(int);
            }
            if (write_inputvars) {
                write_size_bytes += (u.size() + v.size()) * sizeof(double);
            }
            perf_metrics.total_data_written_mb += write_size_bytes / (1024 * 1024);
            
            ++stepAnalysis;
            perf_metrics.total_steps = stepAnalysis;
        }

        // cleanup
        reader.Close();
        writer.Close();
    }

    // Calculate total execution time
    auto end_total = std::chrono::high_resolution_clock::now();
    perf_metrics.total_time = std::chrono::duration<double>(end_total - start_total).count();
    
    // Aggregate performance metrics across all processes
    double total_times[5] = {perf_metrics.total_time, perf_metrics.initialization_time, 
                           perf_metrics.io_read_time, perf_metrics.computation_time, 
                           perf_metrics.io_write_time};
    double max_times[5], min_times[5], avg_times[5];
    size_t total_data[2] = {perf_metrics.total_data_read_mb, perf_metrics.total_data_written_mb};
    size_t sum_data[2];
    
    MPI_Allreduce(total_times, max_times, 5, MPI_DOUBLE, MPI_MAX, comm);
    MPI_Allreduce(total_times, min_times, 5, MPI_DOUBLE, MPI_MIN, comm);
    MPI_Allreduce(total_times, avg_times, 5, MPI_DOUBLE, MPI_SUM, comm);
    MPI_Allreduce(total_data, sum_data, 2, MPI_UNSIGNED_LONG, MPI_SUM, comm);
    
    // Calculate averages
    for (int i = 0; i < 5; i++) {
        avg_times[i] /= comm_size;
    }
    
    // Print detailed performance summary
    if (!rank) {
        std::cout << "\n=== Detailed Performance Summary ===" << std::endl;
        std::cout << std::fixed << std::setprecision(3);
        std::cout << "Metric                    | Max      | Min      | Avg      |" << std::endl;
        std::cout << "--------------------------|----------|----------|----------|" << std::endl;
        std::cout << "Total execution time (s)  | " << std::setw(8) << max_times[0] << " | " << std::setw(8) << min_times[0] << " | " << std::setw(8) << avg_times[0] << " |" << std::endl;
        std::cout << "Initialization time (s)   | " << std::setw(8) << max_times[1] << " | " << std::setw(8) << min_times[1] << " | " << std::setw(8) << avg_times[1] << " |" << std::endl;
        std::cout << "I/O read time (s)         | " << std::setw(8) << max_times[2] << " | " << std::setw(8) << min_times[2] << " | " << std::setw(8) << avg_times[2] << " |" << std::endl;
        std::cout << "Computation time (s)      | " << std::setw(8) << max_times[3] << " | " << std::setw(8) << min_times[3] << " | " << std::setw(8) << avg_times[3] << " |" << std::endl;
        std::cout << "I/O write time (s)        | " << std::setw(8) << max_times[4] << " | " << std::setw(8) << min_times[4] << " | " << std::setw(8) << avg_times[4] << " |" << std::endl;
        std::cout << "=====================================" << std::endl;
        std::cout << "Total steps processed:    " << perf_metrics.total_steps << std::endl;
        std::cout << "Total data read (MB):     " << sum_data[0] << std::endl;
        std::cout << "Total data written (MB):  " << sum_data[1] << std::endl;
        std::cout << "Processes used:           " << comm_size << std::endl;
        
        if (perf_metrics.total_steps > 0 && avg_times[2] > 0 && avg_times[4] > 0) {
            std::cout << "Average time per step:    " << (avg_times[0] - avg_times[1]) / perf_metrics.total_steps << " seconds" << std::endl;
            std::cout << "Read throughput:          " << (sum_data[0] / avg_times[2]) << " MB/s" << std::endl;
            std::cout << "Write throughput:         " << (sum_data[1] / avg_times[4]) << " MB/s" << std::endl;
        }
        std::cout << "=====================================" << std::endl;
    }

    MPI_Barrier(comm);
    MPI_Finalize();
    return 0;
}

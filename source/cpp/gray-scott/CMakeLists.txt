cmake_minimum_required(VERSION 3.22)
project(ADIOS2GrayScott)

# Find required packages
find_package(MPI REQUIRED)
find_package(adios2 REQUIRED)

# Set C++ standard
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Add executable for gray-scott simulation
add_executable(adios2-gray-scott 
    simulation/main.cpp
    simulation/gray-scott.cpp
    simulation/settings.cpp
    simulation/writer.cpp
    simulation/restart.cpp
)

# Link libraries for gray-scott
target_link_libraries(adios2-gray-scott 
    adios2::adios2
    MPI::MPI_CXX
)

# Add executable for pdf-calc analysis
add_executable(adios2-pdf-calc 
    analysis/pdf-calc.cpp
)

# Link libraries for pdf-calc
target_link_libraries(adios2-pdf-calc 
    adios2::adios2
    MPI::MPI_CXX
)

# Include MPI headers
target_include_directories(adios2-gray-scott PRIVATE ${MPI_INCLUDE_PATH})
target_include_directories(adios2-pdf-calc PRIVATE ${MPI_INCLUDE_PATH})
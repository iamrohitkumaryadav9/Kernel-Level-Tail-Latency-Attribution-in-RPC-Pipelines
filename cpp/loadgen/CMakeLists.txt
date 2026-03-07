add_executable(hft-loadgen
    main.cpp
    grpc_worker.cpp
    stats_collector.cpp
    json_output.cpp
)

target_include_directories(hft-loadgen PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

target_link_libraries(hft-loadgen PRIVATE
    order_proto
    pthread
)

# Enable optimizations for timestamp accuracy
target_compile_options(hft-loadgen PRIVATE -O2 -march=native)

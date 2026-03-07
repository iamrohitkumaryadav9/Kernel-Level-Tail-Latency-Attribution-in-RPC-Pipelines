add_executable(hft-execution
    main.cpp
    grpc_server.cpp
)

target_include_directories(hft-execution PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

target_link_libraries(hft-execution PRIVATE
    order_proto
    pthread
)

target_compile_options(hft-execution PRIVATE -O2 -march=native)

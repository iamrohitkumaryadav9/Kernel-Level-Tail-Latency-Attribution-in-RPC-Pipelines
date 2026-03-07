add_executable(hft-analyzer
    main.cpp
    bpf_map_reader.cpp
)

target_include_directories(hft-analyzer PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(hft-analyzer PRIVATE pthread)
target_compile_options(hft-analyzer PRIVATE -O2 -march=native)

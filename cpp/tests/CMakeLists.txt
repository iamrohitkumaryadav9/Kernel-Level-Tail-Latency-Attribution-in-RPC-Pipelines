enable_testing()

find_package(GTest REQUIRED)

# SPSC Ring Buffer tests
add_executable(test_spsc_ring test_spsc_ring.cpp)
target_include_directories(test_spsc_ring PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(test_spsc_ring PRIVATE GTest::GTest GTest::Main pthread)
target_compile_options(test_spsc_ring PRIVATE -O2 -march=native)
add_test(NAME SpscRingTests COMMAND test_spsc_ring)

# HDR Histogram tests
add_executable(test_hdr_histogram test_hdr_histogram.cpp)
target_include_directories(test_hdr_histogram PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(test_hdr_histogram PRIVATE GTest::GTest GTest::Main pthread)
target_compile_options(test_hdr_histogram PRIVATE -O2 -march=native)
add_test(NAME HdrHistogramTests COMMAND test_hdr_histogram)

# Order Book and Memory Pool tests
add_executable(test_order_book test_order_book.cpp)
target_include_directories(test_order_book PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(test_order_book PRIVATE GTest::GTest GTest::Main pthread)
target_compile_options(test_order_book PRIVATE -O2 -march=native)
add_test(NAME OrderBookTests COMMAND test_order_book)

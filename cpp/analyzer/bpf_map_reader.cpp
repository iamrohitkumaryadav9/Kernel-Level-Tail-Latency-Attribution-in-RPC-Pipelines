#include "bpf_map_reader.h"

#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <regex>
#include <chrono>

// Simple HTTP GET using POSIX sockets (no libcurl dependency)
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <unistd.h>

namespace hft {

namespace {

// Minimal HTTP GET — HFT style: no library dependencies, raw sockets
std::string http_get(const std::string& url) {
    // Parse URL: http://host:port/path
    std::string host;
    int port = 80;
    std::string path = "/";

    auto proto_end = url.find("://");
    std::string rest = (proto_end != std::string::npos) ? url.substr(proto_end + 3) : url;

    auto path_pos = rest.find('/');
    if (path_pos != std::string::npos) {
        path = rest.substr(path_pos);
        rest = rest.substr(0, path_pos);
    }
    auto colon = rest.find(':');
    if (colon != std::string::npos) {
        host = rest.substr(0, colon);
        port = std::stoi(rest.substr(colon + 1));
    } else {
        host = rest;
    }

    // Resolve hostname
    struct addrinfo hints{}, *res;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host.c_str(), std::to_string(port).c_str(), &hints, &res) != 0) {
        return "";
    }

    int fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd < 0) { freeaddrinfo(res); return ""; }

    // Set timeout
    struct timeval tv{2, 0}; // 2 second timeout
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    if (connect(fd, res->ai_addr, res->ai_addrlen) < 0) {
        close(fd); freeaddrinfo(res); return "";
    }
    freeaddrinfo(res);

    // Send HTTP request
    std::string req = "GET " + path + " HTTP/1.0\r\nHost: " + host + "\r\n\r\n";
    send(fd, req.c_str(), req.size(), 0);

    // Read response
    std::string response;
    char buf[4096];
    ssize_t n;
    while ((n = recv(fd, buf, sizeof(buf) - 1, 0)) > 0) {
        buf[n] = '\0';
        response += buf;
    }
    close(fd);

    // Strip HTTP headers
    auto body_start = response.find("\r\n\r\n");
    if (body_start != std::string::npos) {
        return response.substr(body_start + 4);
    }
    return response;
}

} // anonymous namespace

BpfMapReader::BpfMapReader(const std::string& metrics_url)
    : metrics_url_(metrics_url) {
    // Test connectivity
    auto body = http_get(metrics_url_);
    connected_ = !body.empty() && body.find("rqdelay") != std::string::npos;
}

EbpfSnapshot BpfMapReader::snapshot() {
    EbpfSnapshot snap;
    snap.timestamp_ns = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());

    auto body = http_get(metrics_url_);
    if (body.empty()) return snap;

    return parse_prometheus(body);
}

EbpfSnapshot BpfMapReader::parse_prometheus(const std::string& body) {
    EbpfSnapshot snap;
    snap.timestamp_ns = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());

    std::istringstream iss(body);
    std::string line;

    while (std::getline(iss, line)) {
        if (line.empty() || line[0] == '#') continue;

        // Parse: rqdelay_bucket_count{bucket="N"} VALUE
        if (line.find("rqdelay_bucket_count{") != std::string::npos) {
            auto bucket_pos = line.find("bucket=\"");
            auto val_pos = line.rfind(' ');
            if (bucket_pos != std::string::npos && val_pos != std::string::npos) {
                int bucket = std::stoi(line.substr(bucket_pos + 8));
                double val = std::stod(line.substr(val_pos + 1));
                if (bucket >= 0 && bucket < 64) {
                    snap.wakeup_delay_hist[bucket] = static_cast<uint64_t>(val);
                }
            }
        }
        // Parse: rqdelay_p50_us VALUE
        else if (line.find("rqdelay_p50_us ") != std::string::npos) {
            snap.wakeup_delay_p50_us = static_cast<uint64_t>(
                std::stod(line.substr(line.rfind(' ') + 1)));
        }
        else if (line.find("rqdelay_p99_us ") != std::string::npos) {
            snap.wakeup_delay_p99_us = static_cast<uint64_t>(
                std::stod(line.substr(line.rfind(' ') + 1)));
        }
        // Parse softirq time
        else if (line.find("rqdelay_softirq_time_ns{") != std::string::npos) {
            for (int i = 0; i < 10; ++i) {
                if (line.find(std::string("vector=\"") + SOFTIRQ_NAMES[i] + "\"") != std::string::npos) {
                    snap.softirq_time_ns[i] = static_cast<uint64_t>(
                        std::stod(line.substr(line.rfind(' ') + 1)));
                    snap.total_softirq_time_ns += snap.softirq_time_ns[i];
                    break;
                }
            }
        }
        // Parse softirq count
        else if (line.find("rqdelay_softirq_count{") != std::string::npos) {
            for (int i = 0; i < 10; ++i) {
                if (line.find(std::string("vector=\"") + SOFTIRQ_NAMES[i] + "\"") != std::string::npos) {
                    snap.softirq_count[i] = static_cast<uint64_t>(
                        std::stod(line.substr(line.rfind(' ') + 1)));
                    snap.total_softirq_count += snap.softirq_count[i];
                    break;
                }
            }
        }
        // TCP retransmit
        else if (line.find("rqdelay_tcp_retransmit_total ") != std::string::npos) {
            snap.tcp_retransmit_total = static_cast<uint64_t>(
                std::stod(line.substr(line.rfind(' ') + 1)));
        }
    }

    // Compute totals for wakeup delay
    for (auto c : snap.wakeup_delay_hist) snap.wakeup_total_events += c;
    snap.wakeup_delay_p50_us = estimate_percentile(snap.wakeup_delay_hist, 0.50);
    snap.wakeup_delay_p99_us = estimate_percentile(snap.wakeup_delay_hist, 0.99);

    return snap;
}

EbpfSnapshot BpfMapReader::delta(const EbpfSnapshot& prev, const EbpfSnapshot& curr) {
    EbpfSnapshot d;
    d.timestamp_ns = curr.timestamp_ns;
    for (int i = 0; i < 64; ++i)
        d.wakeup_delay_hist[i] = curr.wakeup_delay_hist[i] - prev.wakeup_delay_hist[i];
    d.wakeup_delay_p50_us = curr.wakeup_delay_p50_us;
    d.wakeup_delay_p99_us = curr.wakeup_delay_p99_us;
    d.wakeup_total_events = curr.wakeup_total_events - prev.wakeup_total_events;
    for (int i = 0; i < 10; ++i) {
        d.softirq_time_ns[i] = curr.softirq_time_ns[i] - prev.softirq_time_ns[i];
        d.softirq_count[i] = curr.softirq_count[i] - prev.softirq_count[i];
    }
    d.total_softirq_time_ns = curr.total_softirq_time_ns - prev.total_softirq_time_ns;
    d.total_softirq_count = curr.total_softirq_count - prev.total_softirq_count;
    d.tcp_retransmit_total = curr.tcp_retransmit_total - prev.tcp_retransmit_total;
    return d;
}

uint64_t BpfMapReader::estimate_percentile(const std::array<uint64_t, 64>& hist, double q) {
    uint64_t total = 0;
    for (auto c : hist) total += c;
    if (total == 0) return 0;

    uint64_t target = static_cast<uint64_t>(q * static_cast<double>(total));
    if (target == 0) target = 1;

    uint64_t cum = 0;
    for (int i = 0; i < 64; ++i) {
        cum += hist[i];
        if (cum >= target) {
            if (i == 0) return 1;
            uint64_t low = 1ULL << i;
            uint64_t high = 1ULL << (i + 1);
            return (low + high) / 2;
        }
    }
    return 1ULL << 63;
}

} // namespace hft

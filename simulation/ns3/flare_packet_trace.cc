#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"

#include <array>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("FlarePacketTrace");

namespace
{

constexpr std::size_t PATH_COUNT = 3;
constexpr uint32_t PACKET_SIZE_BYTES = 1024;
constexpr double OFFERED_PACKETS_PER_SECOND = 100.0;
constexpr double WARMUP_SECONDS = 1.0;

const std::array<std::string, PATH_COUNT> PATH_NAMES = {"direct", "satellite", "mesh"};
const std::array<std::string, PATH_COUNT> PATH_RATES = {"20Mbps", "5Mbps", "10Mbps"};
const std::array<std::string, PATH_COUNT> PATH_DELAYS = {"10ms", "120ms", "35ms"};
const std::array<double, PATH_COUNT> BASE_ERROR_RATES = {0.01, 0.02, 0.03};
const std::array<double, PATH_COUNT> JAMMED_ERROR_RATES = {0.75, 0.65, 0.70};

struct StepStatistics
{
    uint64_t txPackets{0};
    uint64_t rxPackets{0};
    uint64_t rxBytes{0};
    double delaySumMs{0.0};
};

double g_intervalSeconds = 1.0;
uint32_t g_steps = 0;
std::vector<std::array<StepStatistics, PATH_COUNT>> g_statistics;

int64_t
GetStepForTransmitTime(Time transmitTime)
{
    const double relativeTime = transmitTime.GetSeconds() - WARMUP_SECONDS;
    if (relativeTime < -1e-9)
    {
        return -1;
    }
    return static_cast<int64_t>((relativeTime + 1e-9) / g_intervalSeconds);
}

void
RecordTransmit(uint32_t pathIndex,
               Ptr<const Packet> packet,
               const Address& source,
               const Address& destination)
{
    (void)packet;
    (void)source;
    (void)destination;
    const int64_t step = GetStepForTransmitTime(Simulator::Now());
    if (step >= 0 && static_cast<uint32_t>(step) < g_steps)
    {
        g_statistics[step][pathIndex].txPackets++;
    }
}

void
RecordReceive(uint32_t pathIndex,
              Ptr<const Packet> packet,
              const Address& source,
              const Address& destination)
{
    (void)source;
    (void)destination;
    SeqTsHeader header;
    Ptr<Packet> copy = packet->Copy();
    if (copy->PeekHeader(header) == 0)
    {
        return;
    }

    const int64_t step = GetStepForTransmitTime(header.GetTs());
    if (step < 0 || static_cast<uint32_t>(step) >= g_steps)
    {
        return;
    }
    StepStatistics& statistics = g_statistics[step][pathIndex];
    statistics.rxPackets++;
    statistics.rxBytes += packet->GetSize();
    statistics.delaySumMs += (Simulator::Now() - header.GetTs()).GetMilliSeconds();
}

void
SetErrorRate(Ptr<RateErrorModel> errorModel, double errorRate)
{
    errorModel->SetRate(errorRate);
}

std::vector<std::array<bool, PATH_COUNT>>
BuildJammingPlan(const std::string& scenario, uint32_t steps, uint64_t stream)
{
    std::vector<std::array<bool, PATH_COUNT>> plan(steps);
    Ptr<UniformRandomVariable> random = CreateObject<UniformRandomVariable>();
    random->SetStream(stream);

    for (uint32_t step = 0; step < steps; ++step)
    {
        plan[step].fill(false);
        if (scenario == "clean")
        {
            continue;
        }
        if (scenario == "iid")
        {
            for (std::size_t path = 0; path < PATH_COUNT; ++path)
            {
                plan[step][path] = random->GetValue() < 0.2;
            }
        }
        else if (scenario == "persistent_spot")
        {
            const std::size_t target = (step / 10) % PATH_COUNT;
            plan[step][target] = true;
        }
        else if (scenario == "barrage")
        {
            if (step % 10 < 7)
            {
                plan[step].fill(true);
            }
        }
        else if (scenario == "reactive")
        {
            if (step % 2 == 1)
            {
                plan[step][step % PATH_COUNT] = true;
            }
        }
        else
        {
            throw std::invalid_argument("unsupported scenario: " + scenario);
        }
    }
    return plan;
}

std::string
EscapeJson(const std::string& value)
{
    std::string escaped;
    escaped.reserve(value.size());
    for (const char character : value)
    {
        switch (character)
        {
        case '\\':
            escaped += "\\\\";
            break;
        case '"':
            escaped += "\\\"";
            break;
        case '\b':
            escaped += "\\b";
            break;
        case '\f':
            escaped += "\\f";
            break;
        case '\n':
            escaped += "\\n";
            break;
        case '\r':
            escaped += "\\r";
            break;
        case '\t':
            escaped += "\\t";
            break;
        default:
            if (static_cast<unsigned char>(character) < 0x20)
            {
                std::ostringstream encoded;
                encoded << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<unsigned int>(static_cast<unsigned char>(character));
                escaped += encoded.str();
            }
            else
            {
                escaped.push_back(character);
            }
        }
    }
    return escaped;
}

void
WriteTrace(const std::string& outputPath,
           const std::string& scenario,
           uint32_t seed,
           uint64_t run,
           const std::string& episodeId,
           const std::vector<std::array<bool, PATH_COUNT>>& jammingPlan)
{
    std::ofstream output(outputPath);
    if (!output)
    {
        throw std::runtime_error("cannot open output file: " + outputPath);
    }

    output << std::fixed << std::setprecision(6);
    output << "{\n"
           << "  \"raw_version\": \"flare_ns3_packet_trace_v1\",\n"
           << "  \"simulator\": \"ns-3\",\n"
           << "  \"topology\": \"independent_point_to_point_three_path_v1\",\n"
           << "  \"scenario\": \"" << EscapeJson(scenario) << "\",\n"
           << "  \"seed\": " << seed << ",\n"
           << "  \"run\": " << run << ",\n"
           << "  \"episode_id\": \"" << EscapeJson(episodeId) << "\",\n"
           << "  \"interval_s\": " << g_intervalSeconds << ",\n"
           << "  \"packet_size_bytes\": " << PACKET_SIZE_BYTES << ",\n"
           << "  \"offered_packets_per_second\": " << OFFERED_PACKETS_PER_SECOND << ",\n"
           << "  \"samples\": [\n";

    for (uint32_t step = 0; step < g_steps; ++step)
    {
        output << "    {\"step\": " << step << ", \"timestamp_s\": "
               << (step + 1) * g_intervalSeconds << ", \"paths\": [\n";
        for (std::size_t path = 0; path < PATH_COUNT; ++path)
        {
            const StepStatistics& statistics = g_statistics[step][path];
            const double packetLoss = statistics.txPackets == 0
                                          ? 0.0
                                          : 1.0 - static_cast<double>(statistics.rxPackets) /
                                                      statistics.txPackets;
            const double meanDelayMs = statistics.rxPackets == 0
                                           ? 0.0
                                           : statistics.delaySumMs / statistics.rxPackets;
            const double throughputMbps = statistics.rxBytes * 8.0 /
                                          (g_intervalSeconds * 1000.0 * 1000.0);
            output << "      {\"name\": \"" << PATH_NAMES[path]
                   << "\", \"tx_packets\": " << statistics.txPackets
                   << ", \"rx_packets\": " << statistics.rxPackets
                   << ", \"rx_bytes\": " << statistics.rxBytes
                   << ", \"packet_loss\": " << packetLoss
                   << ", \"mean_delay_ms\": " << meanDelayMs
                   << ", \"throughput_mbps\": " << throughputMbps
                   << ", \"jammed\": " << (jammingPlan[step][path] ? 1 : 0) << "}";
            output << (path + 1 == PATH_COUNT ? "\n" : ",\n");
        }
        output << "    ]}" << (step + 1 == g_steps ? "\n" : ",\n");
    }
    output << "  ]\n}\n";
}

} // namespace

int
main(int argc, char* argv[])
{
    std::string scenario = "persistent_spot";
    std::string outputPath = "flare_ns3_packet_trace.json";
    std::string episodeId;
    uint32_t seed = 42;
    uint64_t run = 1;
    uint32_t steps = 30;
    double intervalSeconds = 1.0;

    CommandLine command(__FILE__);
    command.AddValue("scenario", "clean, iid, persistent_spot, barrage, or reactive", scenario);
    command.AddValue("output", "JSON output path", outputPath);
    command.AddValue("episodeId", "Stable unique episode identifier", episodeId);
    command.AddValue("seed", "ns-3 random seed", seed);
    command.AddValue("run", "ns-3 independent run number", run);
    command.AddValue("steps", "Number of synchronized measurement intervals", steps);
    command.AddValue("interval", "Measurement interval in seconds", intervalSeconds);
    command.Parse(argc, argv);

    if (steps == 0 || intervalSeconds <= 0.0)
    {
        std::cerr << "steps and interval must be positive" << std::endl;
        return 2;
    }

    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(run);
    g_intervalSeconds = intervalSeconds;
    g_steps = steps;
    g_statistics.resize(steps);

    if (episodeId.empty())
    {
        episodeId = scenario + ":ns3:seed=" + std::to_string(seed) + ":run=" +
                    std::to_string(run);
    }

    std::vector<std::array<bool, PATH_COUNT>> jammingPlan;
    try
    {
        jammingPlan = BuildJammingPlan(scenario, steps, 1000 + run);
    }
    catch (const std::invalid_argument& error)
    {
        std::cerr << error.what() << std::endl;
        return 2;
    }

    std::array<Ptr<RateErrorModel>, PATH_COUNT> errorModels;
    InternetStackHelper internet;

    for (std::size_t path = 0; path < PATH_COUNT; ++path)
    {
        NodeContainer nodes;
        nodes.Create(2);
        internet.Install(nodes);

        PointToPointHelper pointToPoint;
        pointToPoint.SetDeviceAttribute("DataRate", StringValue(PATH_RATES[path]));
        pointToPoint.SetChannelAttribute("Delay", StringValue(PATH_DELAYS[path]));
        NetDeviceContainer devices = pointToPoint.Install(nodes);

        Ptr<RateErrorModel> errorModel = CreateObject<RateErrorModel>();
        errorModel->SetUnit(RateErrorModel::ERROR_UNIT_PACKET);
        errorModel->SetRate(BASE_ERROR_RATES[path]);
        errorModel->AssignStreams(2000 + run * PATH_COUNT + path);
        devices.Get(1)->SetAttribute("ReceiveErrorModel", PointerValue(errorModel));
        errorModels[path] = errorModel;

        Ipv4AddressHelper addresses;
        const std::string subnet = "10." + std::to_string(path + 1) + ".0.0";
        addresses.SetBase(subnet.c_str(), "255.255.255.0");
        Ipv4InterfaceContainer interfaces = addresses.Assign(devices);

        const uint16_t port = 5000 + path;
        UdpServerHelper server(port);
        ApplicationContainer serverApplications = server.Install(nodes.Get(1));
        serverApplications.Start(Seconds(0.0));
        serverApplications.Stop(Seconds(WARMUP_SECONDS + steps * intervalSeconds + 1.0));
        Ptr<UdpServer> serverApplication =
            DynamicCast<UdpServer>(serverApplications.Get(0));
        serverApplication->TraceConnectWithoutContext(
            "RxWithAddresses",
            MakeBoundCallback(&RecordReceive, static_cast<uint32_t>(path)));

        UdpClientHelper client(interfaces.GetAddress(1), port);
        const uint32_t maximumPackets =
            static_cast<uint32_t>(steps * OFFERED_PACKETS_PER_SECOND) + 100;
        client.SetAttribute("MaxPackets", UintegerValue(maximumPackets));
        client.SetAttribute("Interval", TimeValue(Seconds(1.0 / OFFERED_PACKETS_PER_SECOND)));
        client.SetAttribute("PacketSize", UintegerValue(PACKET_SIZE_BYTES));
        ApplicationContainer clientApplications = client.Install(nodes.Get(0));
        clientApplications.Start(Seconds(WARMUP_SECONDS));
        clientApplications.Stop(Seconds(WARMUP_SECONDS + steps * intervalSeconds));
        Ptr<UdpClient> clientApplication =
            DynamicCast<UdpClient>(clientApplications.Get(0));
        clientApplication->TraceConnectWithoutContext(
            "TxWithAddresses",
            MakeBoundCallback(&RecordTransmit, static_cast<uint32_t>(path)));
    }

    for (uint32_t step = 0; step < steps; ++step)
    {
        for (std::size_t path = 0; path < PATH_COUNT; ++path)
        {
            const double rate = jammingPlan[step][path] ? JAMMED_ERROR_RATES[path]
                                                       : BASE_ERROR_RATES[path];
            Simulator::Schedule(Seconds(WARMUP_SECONDS + step * intervalSeconds),
                                &SetErrorRate,
                                errorModels[path],
                                rate);
        }
    }

    Simulator::Stop(Seconds(WARMUP_SECONDS + steps * intervalSeconds + 1.0));
    Simulator::Run();
    try
    {
        WriteTrace(outputPath, scenario, seed, run, episodeId, jammingPlan);
    }
    catch (const std::runtime_error& error)
    {
        std::cerr << error.what() << std::endl;
        Simulator::Destroy();
        return 1;
    }
    Simulator::Destroy();

    std::cout << "Wrote " << steps << " synchronized packet intervals to " << outputPath
              << std::endl;
    return 0;
}

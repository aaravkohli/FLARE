// Controlled ns-3 Wi-Fi PHY/mobility packet study, not a UAV/RF field model.
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/wifi-module.h"

#include <cmath>
#include <fstream>
#include <iomanip>
#include <stdexcept>
#include <string>
#include <vector>

using namespace ns3;

struct Sample
{
    double timeSeconds;
    double distanceMeters;
    uint64_t offeredPackets;
    uint64_t receivedPackets;
};

static void
RecordSample(Ptr<UdpClient> client,
             Ptr<PacketSink> sink,
             Ptr<MobilityModel> sourceMobility,
             Ptr<MobilityModel> destinationMobility,
             uint32_t packetSize,
             std::vector<Sample>* samples)
{
    Vector source = sourceMobility->GetPosition();
    Vector destination = destinationMobility->GetPosition();
    const double dx = source.x - destination.x;
    const double dy = source.y - destination.y;
    const double dz = source.z - destination.z;
    samples->push_back({Simulator::Now().GetSeconds(),
                        std::sqrt(dx * dx + dy * dy + dz * dz),
                        client->GetTotalTx() / packetSize,
                        sink->GetTotalRx() / packetSize});
}

int
main(int argc, char* argv[])
{
    std::string scenario = "stationary";
    std::string output;
    uint32_t seed = 7;
    uint32_t run = 1;
    uint32_t steps = 12;
    double interval = 1.0;
    double initialDistance = 15.0;
    double speed = 12.0;
    const uint32_t packetSize = 512;

    CommandLine command(__FILE__);
    command.AddValue("scenario", "stationary or moving_away", scenario);
    command.AddValue("output", "JSON output path", output);
    command.AddValue("seed", "random seed", seed);
    command.AddValue("run", "random run number", run);
    command.AddValue("steps", "one-second sampling steps", steps);
    command.AddValue("interval", "sampling interval in seconds", interval);
    command.AddValue("initialDistance", "initial separation in meters", initialDistance);
    command.AddValue("speed", "moving-away speed in meters per second", speed);
    command.Parse(argc, argv);

    if ((scenario != "stationary" && scenario != "moving_away") || output.empty() ||
        seed == 0 || run == 0 || steps < 2 || interval <= 0.0 ||
        initialDistance <= 0.0 || speed < 0.0)
    {
        throw std::invalid_argument("invalid Wi-Fi study arguments");
    }

    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(run);

    NodeContainer nodes;
    nodes.Create(2); // source UAV proxy and receiving base station
    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211b);
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                 "DataMode",
                                 StringValue("DsssRate1Mbps"),
                                 "ControlMode",
                                 StringValue("DsssRate1Mbps"));
    YansWifiChannelHelper channel;
    channel.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
    channel.AddPropagationLoss("ns3::LogDistancePropagationLossModel");
    YansWifiPhyHelper phy;
    phy.SetChannel(channel.Create());
    WifiMacHelper mac;
    mac.SetType("ns3::AdhocWifiMac");
    NetDeviceContainer devices = wifi.Install(phy, mac, nodes);

    MobilityHelper mobility;
    Ptr<ListPositionAllocator> positions = CreateObject<ListPositionAllocator>();
    positions->Add(Vector(initialDistance, 0.0, 0.0));
    positions->Add(Vector(0.0, 0.0, 0.0));
    mobility.SetPositionAllocator(positions);
    mobility.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
    mobility.Install(nodes);
    Ptr<ConstantVelocityMobilityModel> sourceMobility =
        nodes.Get(0)->GetObject<ConstantVelocityMobilityModel>();
    Ptr<ConstantVelocityMobilityModel> destinationMobility =
        nodes.Get(1)->GetObject<ConstantVelocityMobilityModel>();
    sourceMobility->SetVelocity(Vector(scenario == "moving_away" ? speed : 0.0, 0.0, 0.0));
    destinationMobility->SetVelocity(Vector(0.0, 0.0, 0.0));

    InternetStackHelper internet;
    internet.Install(nodes);
    Ipv4AddressHelper addresses;
    addresses.SetBase("10.80.0.0", "255.255.255.0");
    Ipv4InterfaceContainer interfaces = addresses.Assign(devices);

    PacketSinkHelper sinkHelper("ns3::UdpSocketFactory",
                                InetSocketAddress(Ipv4Address::GetAny(), 9001));
    ApplicationContainer sinkApps = sinkHelper.Install(nodes.Get(1));
    sinkApps.Start(Seconds(0.0));
    sinkApps.Stop(Seconds((steps + 1) * interval));
    UdpClientHelper clientHelper(interfaces.GetAddress(1), 9001);
    clientHelper.SetAttribute("MaxPackets", UintegerValue(1000000));
    clientHelper.SetAttribute("Interval", TimeValue(MilliSeconds(20)));
    clientHelper.SetAttribute("PacketSize", UintegerValue(packetSize));
    ApplicationContainer clientApps = clientHelper.Install(nodes.Get(0));
    clientApps.Start(Seconds(0.2));
    clientApps.Stop(Seconds(steps * interval));
    Ptr<UdpClient> client = DynamicCast<UdpClient>(clientApps.Get(0));
    Ptr<PacketSink> sink = DynamicCast<PacketSink>(sinkApps.Get(0));

    std::vector<Sample> samples;
    for (uint32_t step = 1; step <= steps; ++step)
    {
        Simulator::Schedule(Seconds(step * interval),
                            &RecordSample,
                            client,
                            sink,
                            sourceMobility,
                            destinationMobility,
                            packetSize,
                            &samples);
    }
    Simulator::Stop(Seconds(steps * interval + 0.1));
    Simulator::Run();

    std::ofstream file(output);
    if (!file)
    {
        throw std::runtime_error("unable to open Wi-Fi study output");
    }
    file << std::fixed << std::setprecision(6);
    file << "{\n  \"schema_version\": \"flare_ns3_wifi_mobility_v1\",\n"
         << "  \"evidence_category\": \"packet_simulation\",\n"
         << "  \"topology\": \"two_node_80211b_adhoc_log_distance_v1\",\n"
         << "  \"scenario\": \"" << scenario << "\",\n"
         << "  \"seed\": " << seed << ",\n"
         << "  \"run\": " << run << ",\n"
         << "  \"packet_size_bytes\": " << packetSize << ",\n"
         << "  \"initial_distance_meters\": " << initialDistance << ",\n"
         << "  \"speed_mps\": " << (scenario == "moving_away" ? speed : 0.0) << ",\n"
         << "  \"interval_seconds\": " << interval << ",\n"
         << "  \"samples\": [\n";
    uint64_t previousOffered = 0;
    uint64_t previousReceived = 0;
    for (size_t index = 0; index < samples.size(); ++index)
    {
        const Sample& sample = samples[index];
        uint64_t offered = sample.offeredPackets - previousOffered;
        uint64_t received = sample.receivedPackets - previousReceived;
        previousOffered = sample.offeredPackets;
        previousReceived = sample.receivedPackets;
        // Cumulative delivery avoids misleading ratios when a packet sent in
        // one interval arrives in the next interval.
        double pdr = sample.offeredPackets > 0
                         ? static_cast<double>(sample.receivedPackets) / sample.offeredPackets
                         : 0.0;
        file << "    {\"step\": " << index + 1
             << ", \"time_seconds\": " << sample.timeSeconds
             << ", \"distance_meters\": " << sample.distanceMeters
             << ", \"offered_packets\": " << offered
             << ", \"received_packets\": " << received
             << ", \"cumulative_offered_packets\": " << sample.offeredPackets
             << ", \"cumulative_received_packets\": " << sample.receivedPackets
             << ", \"pdr\": " << pdr << "}"
             << (index + 1 < samples.size() ? "," : "") << "\n";
    }
    file << "  ]\n}\n";
    Simulator::Destroy();
    return 0;
}

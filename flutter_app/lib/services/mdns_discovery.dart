import 'dart:async';

import 'package:multicast_dns/multicast_dns.dart';

import '../models.dart';

class MdnsDiscovery {
  static const _service = '_ai-recorder._tcp.local';

  Future<List<LanDevice>> discover({
    Duration timeout = const Duration(seconds: 5),
  }) async {
    final client = MDnsClient();
    final devices = <String, LanDevice>{};
    await client.start();
    try {
      await for (final pointer
          in client
              .lookup<PtrResourceRecord>(
                ResourceRecordQuery.serverPointer(_service),
              )
              .timeout(timeout)) {
        await for (final server
            in client
                .lookup<SrvResourceRecord>(
                  ResourceRecordQuery.service(pointer.domainName),
                )
                .timeout(timeout)) {
          await for (final address
              in client
                  .lookup<IPAddressResourceRecord>(
                    ResourceRecordQuery.addressIPv4(server.target),
                  )
                  .timeout(timeout)) {
            final url = 'http://${address.address.address}:${server.port}';
            devices[url] = LanDevice(name: pointer.domainName, address: url);
          }
        }
      }
    } on TimeoutException {
      // Discovery normally ends by timeout because mDNS is a stream.
    } finally {
      client.stop();
    }
    return devices.values.toList();
  }
}

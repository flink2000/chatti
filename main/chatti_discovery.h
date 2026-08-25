#ifndef CHATTI_DISCOVERY_H_
#define CHATTI_DISCOVERY_H_

#include <string>

namespace chatti {

// Ask the LAN whether a Chatti server is around and build its OTA URL from the
// answer, e.g. "http://192.168.1.42:8003/xiaozhi/ota/". Empty string if
// nothing replied within the timeout.
//
// The point is that nobody has to type an address. The device used to keep the
// server's IP in NVS, which breaks the moment the router hands the PC a
// different one - and nothing in the stack notices (see CLAUDE.md).
// Asking the network is the fix; it costs one multicast query at boot.
std::string DiscoverOtaUrl(uint32_t timeout_ms = 3000);

}  // namespace chatti

#endif  // CHATTI_DISCOVERY_H_

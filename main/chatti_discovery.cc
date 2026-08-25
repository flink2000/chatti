#include "chatti_discovery.h"

#include <cstdio>
#include <cstring>

#include <esp_log.h>
#include <mdns.h>

#define TAG "ChattiDiscovery"

namespace chatti {

namespace {

// Must match announce.py in chatti/control. Deliberately not _http._tcp: the
// device must not stumble into a printer.
constexpr const char* kServiceType = "_chatti";
constexpr const char* kProto = "_tcp";
constexpr const char* kDefaultPath = "/xiaozhi/ota/";

// The TXT record carries the path so a future server can move it without a new
// firmware. Falls back to the value above when the key is missing.
std::string PathFrom(const mdns_result_t* r) {
    for (size_t i = 0; i < r->txt_count; i++) {
        if (r->txt[i].key != nullptr && strcmp(r->txt[i].key, "path") == 0 &&
            r->txt[i].value != nullptr && r->txt[i].value[0] != '\0') {
            return std::string(r->txt[i].value);
        }
    }
    return kDefaultPath;
}

}  // namespace

std::string DiscoverOtaUrl(uint32_t timeout_ms) {
    // mdns_init() is safe to call repeatedly - it returns ESP_ERR_INVALID_STATE
    // when the service is already up, which is not an error for us. We do not
    // call mdns_free() afterwards: nothing else in this firmware uses mDNS, and
    // tearing the responder down between retries would only cost time.
    esp_err_t err = mdns_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "mdns_init failed: %s", esp_err_to_name(err));
        return "";
    }

    mdns_result_t* results = nullptr;
    err = mdns_query_ptr(kServiceType, kProto, timeout_ms, 4, &results);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "query failed: %s", esp_err_to_name(err));
        return "";
    }
    if (results == nullptr) {
        ESP_LOGI(TAG, "no %s%s server on this network", kServiceType, kProto);
        return "";
    }

    std::string url;
    for (mdns_result_t* r = results; r != nullptr && url.empty(); r = r->next) {
        // Only an A record is any use here: the ESP talks IPv4 only, and the
        // whole reason this exists is that the address must be reachable.
        for (mdns_ip_addr_t* a = r->addr; a != nullptr; a = a->next) {
            if (a->addr.type != ESP_IPADDR_TYPE_V4) {
                continue;
            }
            char buf[96];
            snprintf(buf, sizeof(buf), "http://" IPSTR ":%u%s",
                     IP2STR(&a->addr.u_addr.ip4), (unsigned)r->port,
                     PathFrom(r).c_str());
            url = buf;
            ESP_LOGI(TAG, "found server at %s (host %s)", url.c_str(),
                     r->hostname ? r->hostname : "?");
            break;
        }
    }

    mdns_query_results_free(results);
    return url;
}

}  // namespace chatti

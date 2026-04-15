import json
import urllib.parse
from base64 import urlsafe_b64decode

urls = [
    "hysteria2://YOUR_PASSWORD@your-server.com:20520?sni=your-server.com#Example-Node",
    "vless://YOUR_UUID@your-server.com:10031?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=YOUR_PUBLIC_KEY&sid=YOUR_SHORT_ID&type=grpc&serviceName=update#Example-VLESS"
]

outbounds = []
tags = []

for u in urls:
    parsed = urllib.parse.urlparse(u)
    scheme = parsed.scheme
    tag = urllib.parse.unquote(parsed.fragment)
    tags.append(tag)
    
    query = urllib.parse.parse_qs(parsed.query)
    
    if scheme == "hysteria2":
        node = {
            "type": "hysteria2",
            "tag": tag,
            "server": parsed.hostname,
            "server_port": parsed.port,
            "password": urllib.parse.unquote(parsed.username) if parsed.username else "",
            "tls": {
                "enabled": True,
                "server_name": query.get("sni", [parsed.hostname])[0]
            }
        }
        if "obfs" in query:
            node["obfs"] = {
                "type": query["obfs"][0],
                "password": query.get("obfs-password", [""])[0]
            }
        outbounds.append(node)
        
    elif scheme == "vless":
        node = {
            "type": "vless",
            "tag": tag,
            "server": parsed.hostname,
            "server_port": parsed.port,
            "uuid": urllib.parse.unquote(parsed.username) if parsed.username else "",
            "tls": {
                "enabled": True,
                "server_name": query.get("sni", [""])[0],
                "reality": {
                    "enabled": True,
                    "public_key": query.get("pbk", [""])[0],
                    "short_id": query.get("sid", [""])[0]
                },
                "utls": {
                    "enabled": True,
                    "fingerprint": query.get("fp", ["chrome"])[0]
                }
            },
            "transport": {
                "type": query.get("type", ["grpc"])[0],
                "service_name": query.get("serviceName", [""])[0]
            }
        }
        outbounds.append(node)
        
    elif scheme == "tuic":
        password = parsed.password if parsed.password else urllib.parse.unquote(parsed.username)
        uuid_str = urllib.parse.unquote(parsed.username)
        if ":" in urllib.parse.unquote(parsed.netloc.split("@")[0]):
            parts = urllib.parse.unquote(parsed.netloc.split("@")[0]).split(":")
            uuid_str = parts[0]
            password = parts[1]

        node = {
            "type": "tuic",
            "tag": tag,
            "server": parsed.hostname,
            "server_port": parsed.port,
            "uuid": uuid_str,
            "password": password,
            "tls": {
                "enabled": True,
                "server_name": query.get("sni", [parsed.hostname])[0],
                "alpn": query.get("alpn", ["h3"])
            }
        }
        if "congestion_control" in query:
            node["congestion_control"] = query["congestion_control"][0]
        outbounds.append(node)
        
    elif scheme == "ss":
        raw_info = urllib.parse.unquote(parsed.netloc.split("@")[0])
        import string
        def decode_base64(s):
            s += "=" * ((4 - len(s) % 4) % 4)
            return urlsafe_b64decode(s).decode("utf-8")
            
        decoded = ""
        pd = parsed.netloc.split("@")
        if len(pd) > 1:
             user_info = pd[0]
             # ss uri could be base64
             try:
                 decoded = decode_base64(user_info)
             except:
                 decoded = user_info
        
        parts = decoded.split(":")
        node = {
            "type": "shadowsocks",
            "tag": tag,
            "server": parsed.hostname,
            "server_port": parsed.port,
            "method": parts[0] if len(parts) > 0 else "2022-blake3-aes-128-gcm"
        }
        # In this specific ss URL: MjAyMi1ibGFrZTMtYWVzLTEyOC1nY206TWpBeU5qSnJOWE42V1d0SFFWWlBlQT09OlJVWkRRemhGTlVRdFF6UTJOQzAwUkE9PQ
        # base64 decodes to 2022-blake3-aes-128-gcm:MjAyNjJrNXN6WWtHQVZeA==:RUZDQzhFNFqQtQzQ2NC00RA==
        if len(parts) >= 3:
             node["password"] = parts[1] + ":" + parts[2]
        else:
             node["password"] = "MjAyNjJrNXN6WWtHQVZeA==:RUZDQzhFNFqQtQzQ2NC00RA=="
             
        outbounds.append(node)


outbounds.insert(0, {
    "type": "selector",
    "tag": "Proxy-Selector",
    "outbounds": tags,
    "default": "HK3-HY2"
})

outbounds.append({"type": "direct", "tag": "direct"})

config = {
    "log": {
        "level": "info",
        "timestamp": True
    },
    "experimental": {
        "clash_api": {
            "external_controller": "0.0.0.0:9020",
            "external_ui": "ui",
            "secret": "phantom123",
            "default_mode": "rule"
        }
    },
    "inbounds": [
        {
            "type": "http",
            "tag": "http-in",
            "listen": "0.0.0.0",
            "listen_port": 2080
        }
    ],
    "outbounds": outbounds,
    "route": {
        "rules": [
            {
                "inbound": ["http-in"],
                "outbound": "Proxy-Selector"
            }
        ],
        "final": "Proxy-Selector"
    }
}

with open("d:\\project\\proxy\\singbox_config.json", "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)

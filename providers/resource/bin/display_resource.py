#!/usr/bin/env python3

import os
import re
import sys
import subprocess

CONNECTOR_RE = re.compile(
    r"\n(?P<Name>[\w\-]+)"
    r"(?:\s+.*?"
    r"SignalFormat:\s+(?P<SignalFormat>[\w\-_]+)\s+.*?"
    r"ConnectorType:\s+(?P<ConnectorType>[\w\-_]+))?", re.S)
SVIDEO_RE = re.compile(r"s\-?video|din|cv", re.I)
DP_RE = re.compile(r"dp|displayport", re.I)


def main():
    try:
        if os.getenv('XDG_SESSION_TYPE') == 'wayland':
            xrandr_output = subprocess.check_output(
                ["gnome-randr"], universal_newlines=True)
        else:
            xrandr_output = subprocess.check_output(
                ["xrandr", "-q", "--verbose"], universal_newlines=True)
    except subprocess.CalledProcessError:
        return 0

    xrandr_output = "\n" + ''.join(xrandr_output.splitlines(True)[1:])
    supported_connections = dict()

    supported_connections['vga'] = 'not-supported'
    supported_connections['dvi'] = 'not-supported'
    supported_connections['hdmi'] = 'not-supported'
    supported_connections['dp'] = 'not-supported'
    supported_connections['svideo'] = 'not-supported'
    supported_connections['rca'] = 'not-supported'
    supported_connections['tv'] = 'not-supported'

    for m in CONNECTOR_RE.finditer(xrandr_output):
        name = m.group('Name').lower()
        signal_format = connector_type = ''  # RandR 1.3 only
        if m.group('SignalFormat'):
            signal_format = m.group('SignalFormat').lower()
        if m.group('ConnectorType'):
            connector_type = m.group('ConnectorType').lower()

        if name.startswith('vga'):
            supported_connections['vga'] = 'supported'

        elif name.startswith('dvi'):
            supported_connections['dvi'] = 'supported'

        elif DP_RE.match(name):
            # HDMI uses TMDS links, DisplayPort (DP) uses LVDS pairs (lanes)
            # to transmit micro data packets.
            # Reported by NVIDIA proprietary drivers (version >= 302.17)
            if signal_format == 'tmds':
                supported_connections['hdmi'] = 'supported'
            else:
                supported_connections['dp'] = 'supported'

        elif name.startswith('hdmi'):
            supported_connections['hdmi'] = 'supported'

        elif SVIDEO_RE.match(name):
            supported_connections['svideo'] = 'supported'

        elif name.startswith('rca'):
            supported_connections['rca'] = 'supported'

        elif name.startswith('tv'):
            if SVIDEO_RE.match(signal_format):
                supported_connections['svideo'] = 'supported'
            else:
                supported_connections['tv'] = 'supported'

        # ATI/AMD proprietary FGLRX graphics driver codenames:
        elif name.startswith('crt') or name.startswith('dfp'):
            if connector_type.startswith('dvi'):
                supported_connections['dvi'] = 'supported'
            elif connector_type.startswith('vga'):
                supported_connections['vga'] = 'supported'
            elif DP_RE.match(connector_type) and DP_RE.match(signal_format):
                supported_connections['dp'] = 'supported'
            else:
                # HDMI ports may appear as unknown
                # (for both signal format and connector type).
                supported_connections['hdmi'] = 'supported'

    # HDMI ports are often reported as DP due to drivers/firmwares
    # Let's assume both are present
    # See https://bugs.launchpad.net/plainbox-provider-resource/+bug/1757359
    if supported_connections['dp'] == 'supported':
        supported_connections['hdmi'] = 'supported'

    for connector in supported_connections.items():
        print("{}: {}".format(connector[0], connector[1]))

    return 0


if __name__ == "__main__":
    sys.exit(main())

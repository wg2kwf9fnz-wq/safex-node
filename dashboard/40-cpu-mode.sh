#!/bin/sh
# Starts the tiny CGI server (loopback only; nginx proxies /api/cpu-mode to it).
mkdir -p /control
busybox-extras httpd -p 127.0.0.1:8081 -h /srv/cgi

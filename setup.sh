#!/bin/bash

# Creation of the venv.

python3 -m venv ./venv
./venv/bin/pip3 install -r requirements.txt


# Manager compilation

mkdir -p ./bin
gcc -I./lib -o ./bin/Manager ./src/Manager/Manager.c ./lib/dbus_tracker.c -lsystemd


# Systemd User Directory Setup

mkdir -p ~/.config/systemd/user

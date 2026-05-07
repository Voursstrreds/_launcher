#!/bin/bash

# Creation of the venv.

python3 -m venv ./venv
source ./venv/bin/activate
pip3 install -r requirements.txt
deactivate


# Generic task compilation

cd ./lib/generic-tasks
source compile.sh
cd ../../


# Manager compilation

mkdir -p ./bin
gcc -I./lib -o ./bin/Manager ./src/Manager/Manager.c ./lib/Unit_Starter.c -lsystemd


# Systemd User Directory Setup

mkdir -p ~/.config/systemd/user

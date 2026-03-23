#!/bin/bash
# This script runs once during MongoDB container initialization.
# The healthcheck handles rs.initiate() since it needs mongod to be up first.
echo "Replica set rs0 will be initialized by the healthcheck."

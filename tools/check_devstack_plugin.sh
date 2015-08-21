#!/bin/bash -ex

# Sleep long enough for the below checks to have a chance
# at being completed.
sleep 15m
# Check that snapshot image built
nodepool image-list | grep ready | grep trusty-server
# check that dib image built
nodepool image-list | grep ready | grep ubuntu-dib
# check snapshot image was bootable
nodepool list | grep ready | grep trusty-server
# check dib image was bootable
nodepool list | grep ready | grep ubuntu-dib

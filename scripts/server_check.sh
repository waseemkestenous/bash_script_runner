#!/usr/bin/env bash

echo "Welcome to Server Check Script"
echo "What is the server serial number?"
read SERIAL

echo "What is the failure type? Type: power, network, disk, other"
read FAILURE

if [ "$FAILURE" = "power" ]; then
    echo "Did you check the power cable? yes/no"
    read POWER_CABLE

    if [ "$POWER_CABLE" = "yes" ]; then
        echo "Try replacing PSU. Did that fix it? yes/no"
        read PSU_RESULT

        if [ "$PSU_RESULT" = "yes" ]; then
            echo "Final Result: Server $SERIAL fixed by replacing PSU."
        else
            echo "Final Result: Server $SERIAL needs motherboard/power-path escalation."
        fi
    else
        echo "Final Result: Please check power cable first for server $SERIAL."
    fi

elif [ "$FAILURE" = "network" ]; then
    echo "Is the link light on? yes/no"
    read LINK_LIGHT

    if [ "$LINK_LIGHT" = "yes" ]; then
        echo "Final Result: Server $SERIAL may have VLAN/IP/config issue."
    else
        echo "Final Result: Server $SERIAL may have cable/NIC/switch-port issue."
    fi

elif [ "$FAILURE" = "disk" ]; then
    echo "How many disks failed?"
    read DISK_COUNT
    echo "Final Result: Server $SERIAL has $DISK_COUNT disk issue(s). Create storage ticket."

else
    echo "Describe the issue shortly:"
    read ISSUE
    echo "Final Result: Server $SERIAL reported issue: $ISSUE"
fi

exit 0

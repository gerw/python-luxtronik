"""Luxtronik heatpump interface."""
# -*- coding: utf-8 -*-
# region Imports
import logging
import struct
import asyncio

from luxtronik.calculations import Calculations
from luxtronik.parameters import Parameters
from luxtronik.visibilities import Visibilities

# endregion Imports

LOGGER = logging.getLogger("ALuxtronik")

# Wait time (in seconds) after writing parameters to give controller
# some time to re-calculate values, etc.
WAIT_TIME_AFTER_PARAMETER_WRITE = 1

class ALuxtronik:
    """Main luxtronik class as an async context manager."""

    def __init__(self, host, port=8889, safe=True):
        self._lock = asyncio.Lock()
        self._host = host
        self._port = port
        self._safe = safe
        self._reader = None
        self._writer = None
        self.parameters = Parameters(safe=self._safe)
        self.calculations = Calculations()

    async def __aenter__(self):
        # Should not be called twice, otherwise we overwrite _reader and _writer!
        # Maybe we could simply use a lock?
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        return self

    async def __aexit__(self, *excinfo):
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
        self._reader = None
        self._writer = None

    async def read(self):
        """Read data from heatpump."""
        await self._read_after_write(write=False)

    async def write(self):
        """Write parameter to heatpump."""
        await self._read_after_write(write=True)

    async def _read_after_write(self, write):
        """
        Read and/or write value from and/or to heatpump.
        This method is essentially a wrapper for the _read() and _write()
        methods.
        Locking is being used to ensure that only a single socket operation is
        performed at any point in time. This helps to avoid issues with the
        Luxtronik controller, which seems unstable otherwise.
        If write is true, all parameters will be written to the heat pump
        prior to reading back in all data from the heat pump. If write is
        false, no data will be written, but all available data will be read
        from the heat pump.
        :param Parameters() parameters  Parameter dictionary to be written
                          to the heatpump before reading all available data
                          from the heatpump. At 'None' it is read only.
        """

        async with self._lock:

            # Here, we should handle a reconnect??
            if write:
                await self._write()

                # Give the heatpump a short time to handle the value changes/calculations:
                await asyncio.sleep(WAIT_TIME_AFTER_PARAMETER_WRITE)

            await self._read()

    async def _read(self):
        await self._read_parameters()
        await self._read_calculations()
        # visibilities = await self._read_visibilities()
        # return calculations, parameters, visibilities

    async def _write(self):
        for index, value in self.parameters.queue.items():
            if not isinstance(index, int) or not isinstance(value, int):
                LOGGER.warning(
                    "%s: Parameter id '%s' or value '%s' invalid!",
                    self._host,
                    index,
                    value,
                )
                continue
            LOGGER.info("%s: Parameter '%d' set to '%s'", self._host, index, value)
            await self._send_ints(3002, index, value)

            cmd = await self._recv_int()
            LOGGER.debug("%s: Command %s", self._host, cmd)
            val = await self._recv_int()
            LOGGER.debug("%s: Value %s", self._host, val)

        # Flush queue after writing all values
        self.parameters.queue = {}

    async def _read_parameters(self):
        await self._send_ints(3003, 0)

        cmd = await self._recv_int()
        LOGGER.debug("%s: Command %s", self._host, cmd)

        length = await self._recv_int()
        LOGGER.debug("%s: Length %s", self._host, length)

        data = []
        for _ in range(0, length):
            try:
                val = await self._recv_int()
                data.append(val)
            except struct.error as err:
                # not logging this as error as it would be logged on every read cycle
                LOGGER.debug("%s: %s", self._host, err)
        LOGGER.info("%s: Read %d parameters", self._host, length)
        self.parameters.parse(data)

    async def _read_calculations(self):
        await self._send_ints(3004, 0)

        cmd = await self._recv_int()
        LOGGER.debug("%s: Command %s", self._host, cmd)

        stat = await self._recv_int()
        LOGGER.debug("%s: Stat %s", self._host, stat)

        length = await self._recv_int()
        LOGGER.debug("%s: Length %s", self._host, length)

        data = []
        for _ in range(0, length):
            try:
                val = await self._recv_int()
                data.append(val)
            except struct.error as err:
                # not logging this as error as it would be logged on every read cycle
                LOGGER.debug("%s: %s", self._host, err)
        LOGGER.info("%s: Read %d calculations", self._host, length)
        self.calculations.parse(data)

    # def _read_visibilities(self):
    #     data = []
    #     self._socket.sendall(struct.pack(">ii", 3005, 0))
    #     cmd = struct.unpack(">i", self._socket.recv(4))[0]
    #     LOGGER.debug("%s: Command %s", self._host, cmd)
    #     length = struct.unpack(">i", self._socket.recv(4))[0]
    #     LOGGER.debug("%s: Length %s", self._host, length)
    #     for _ in range(0, length):
    #         try:
    #             data.append(struct.unpack(">b", self._socket.recv(1))[0])
    #         except struct.error as err:
    #             # not logging this as error as it would be logged on every read cycle
    #             LOGGER.debug("%s: %s", self._host, err)
    #     LOGGER.info("%s: Read %d visibilities", self._host, length)
    #     visibilities = Visibilities()
    #     visibilities.parse(data)
    #     return visibilities

    async def _send_ints(self, *ints):
        "Low-level helper to send a tuple of ints"
        data = struct.pack(">" + "i" * len(ints), *ints)
        LOGGER.debug("%s: sending %s", self._host, data)
        self._writer.write(data)
        await self._writer.drain()

    async def _recv_int(self):
        "Low-level helper to receive an int"
        data = await self._reader.readexactly(4)
        value = struct.unpack(">i", data)[0]
        return value

    async def _recv_char(self):
        "Low-level helper to receive a signed char"
        data = await self._reader.readexactly(1)
        value = struct.unpack(">b", data)[0]
        return value

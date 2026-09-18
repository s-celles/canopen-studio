# Telemetry & Real-Time Oscilloscope

The **Telemetry Plotter** in CAN & CANopen Studio provides a real-time multi-trace oscilloscope for inspecting signals and reverse-engineering proprietary CAN protocols.

---

## Capabilities

1. **Simultaneous 8-Byte Payload Tracing (B0 to B7)**:
   - When inspecting an unknown CAN identifier (e.g. `0x123`), you can plot all individual 8 payload bytes on the same graph in real-time.
   - Each byte is assigned a distinct color palette.
   - Ideal for identifying which byte correlates to accelerator position, steering angle, or temperature sensors.

2. **Word Mode (16-bit & 32-bit Integers)**:
   - Combine bytes into 16-bit or 32-bit words (Little Endian or Big Endian, Signed or Unsigned).
   - Visualize high-resolution physical signals directly.

3. **Decoded Physical Signals**:
   - Any signal emitted by application decoders (e.g., `motor_speed_rpm`, `heatsink_temp_c`, `target_torque`) can be selected and plotted with calibrated physical engineering units.

4. **Interactive Controls**:
   - **Pause / Resume**: Freeze plotting to inspect waveform details.
   - **Clear**: Reset history buffer (up to 1500 live points).
   - **Navigation Toolbar**: Pan, zoom, autoscale, and save PNG screenshots directly from matplotlib.

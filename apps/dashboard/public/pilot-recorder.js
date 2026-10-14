/* global AudioWorkletProcessor, registerProcessor */
/* PCM capture only after getUserMedia. Outputs silence: no microphone loopback. */
class PilotRecorder extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = new Float32Array(4096); this.offset = 0; }
  process(inputs) {
    const samples = inputs[0]?.[0];
    if (!samples) return true;
    for (const value of samples) {
      this.buffer[this.offset++] = value;
      if (this.offset === this.buffer.length) {
        this.port.postMessage(this.buffer, [this.buffer.buffer]);
        this.buffer = new Float32Array(4096); this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('pilot-recorder', PilotRecorder);

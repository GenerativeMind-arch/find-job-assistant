/**
 * 浏览器语音录音工具 —— 录制麦克风音频并编码为 WAV（PCM 16bit）base64。
 *
 * qwen-audio-turbo 支持 WAV 格式，因此用 Web Audio API 采集 PCM 后手动编码 WAV，
 * 避免 MediaRecorder 产出 webm/opus 容器不被语音模型解析的问题。
 *
 * 用法:
 *   const recorder = new AudioRecorder()
 *   await recorder.start()          // 请求麦克风权限并开始录音
 *   recorder.stop()                 // 停止
 *   const base64 = await recorder.getWavBase64()  // 获取 WAV base64
 */

export class AudioRecorder {
  constructor() {
    this.stream = null
    this.audioContext = null
    this.processor = null
    this.source = null
    this.chunks = []
    this.sampleRate = 16000 // 输出采样率（qwen-audio 兼容）
  }

  /** 开始录音 */
  async start() {
    if (this.stream) return
    this.chunks = []

    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    this.audioContext = new (window.AudioContext || window.webkitAudioContext)()
    const ctx = this.audioContext

    this.source = ctx.createMediaStreamSource(this.stream)

    // ScriptProcessorNode：兼容性好，实时采集 PCM
    const bufferSize = 4096
    this.processor = ctx.createScriptProcessor(bufferSize, 1, 1)
    this.processor.onaudioprocess = (e) => {
      const input = e.inputBuffer.getChannelData(0)
      // 将采样率从 ctx.sampleRate 重采样到 this.sampleRate
      this.chunks.push(new Float32Array(input))
    }

    this.source.connect(this.processor)
    this.processor.connect(ctx.destination)
  }

  /** 停止录音 */
  stop() {
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop())
      this.stream = null
    }
    if (this.processor) {
      this.processor.disconnect()
      this.processor = null
    }
    if (this.source) {
      this.source.disconnect()
      this.source = null
    }
    if (this.audioContext) {
      this.audioContext.close().catch(() => {})
      this.audioContext = null
    }
  }

  /** 是否有录音数据 */
  hasData() {
    return this.chunks.length > 0
  }

  /** 获取 WAV base64（PCM 16bit, 16000Hz 单声道） */
  getWavBase64() {
    if (!this.chunks.length) return ''

    // 拼接所有 chunk，重采样到 16000Hz
    const all = []
    let totalLen = 0
    for (const chunk of this.chunks) {
      all.push(chunk)
      totalLen += chunk.length
    }
    const raw = new Float32Array(totalLen)
    let offset = 0
    for (const chunk of all) {
      raw.set(chunk, offset)
      offset += chunk.length
    }

    // 重采样：ctx.sampleRate → 16000
    const outRate = this.sampleRate
    const ctxRate = this.audioContext ? this.audioContext.sampleRate : outRate
    const resampled = this._resample(raw, ctxRate, outRate)

    // 编码 WAV
    const wav = this._encodeWav(resampled, outRate)
    return this._bytesToBase64(wav)
  }

  /** 线性重采样 */
  _resample(input, fromRate, toRate) {
    if (fromRate === toRate) return input
    const ratio = fromRate / toRate
    const newLen = Math.floor(input.length / ratio)
    const out = new Float32Array(newLen)
    for (let i = 0; i < newLen; i++) {
      const idx = Math.floor(i * ratio)
      out[i] = input[Math.min(idx, input.length - 1)]
    }
    return out
  }

  /** 编码 WAV（PCM 16bit 单声道） */
  _encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2)
    const view = new DataView(buffer)

    // RIFF header
    this._writeStr(view, 0, 'RIFF')
    view.setUint32(4, 36 + samples.length * 2, true)
    this._writeStr(view, 8, 'WAVE')
    this._writeStr(view, 12, 'fmt ')
    view.setUint32(16, 16, true) // fmt chunk size
    view.setUint16(20, 1, true) // PCM
    view.setUint16(22, 1, true) // mono
    view.setUint32(24, sampleRate, true)
    view.setUint32(28, sampleRate * 2, true) // byte rate
    view.setUint16(32, 2, true) // block align
    view.setUint16(34, 16, true) // bits per sample
    this._writeStr(view, 36, 'data')
    view.setUint32(40, samples.length * 2, true)

    // PCM data
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]))
      view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
    }

    return new Uint8Array(buffer)
  }

  _writeStr(view, offset, str) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i))
    }
  }

  /** bytes → base64 */
  _bytesToBase64(bytes) {
    let binary = ''
    const chunkSize = 0x8000
    for (let i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize))
    }
    return btoa(binary)
  }
}

/** 简易封装：录音并返回 WAV base64（Promise） */
export function recordAudioOnce(maxDurationMs = 60000) {
  return new Promise((resolve, reject) => {
    const rec = new AudioRecorder()
    rec
      .start()
      .then(() => {
        const timer = setTimeout(() => {
          rec.stop()
          resolve(rec.getWavBase64())
        }, maxDurationMs)
        // 允许提前手动停止由调用方控制，这里简化：定时自动停止
        rec._done = () => {
          clearTimeout(timer)
          rec.stop()
          resolve(rec.getWavBase64())
        }
      })
      .catch((err) => {
        rec.stop()
        reject(err)
      })
  })
}

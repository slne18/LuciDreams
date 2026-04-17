/**
 * Copy web app from Flask app into Capacitor www.
 * Replaces {{ url_for('static', filename='X') }} with "static/X".
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const SRC_HTML = path.join(ROOT, 'templates', 'luci.html');
const SRC_STATIC = path.join(ROOT, 'static');
const DST_WWW = path.join(__dirname, 'www');
const DST_STATIC = path.join(DST_WWW, 'static');
const DST_SOUNDS = path.join(DST_WWW, 'sounds');
const SRC_SOUNDS = path.join(SRC_STATIC, 'sounds');
const DST_IOS_SOUNDS = path.join(__dirname, 'ios', 'App', 'App', 'sounds');
const DST_IOS_ROOT = path.join(__dirname, 'ios', 'App', 'App');

if (!fs.existsSync(SRC_HTML)) {
  console.error('Missing', SRC_HTML);
  process.exit(1);
}

// Ensure dirs
[DST_WWW, DST_STATIC, DST_SOUNDS].forEach(function (dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
});

// Copy and rewrite index.html
let html = fs.readFileSync(SRC_HTML, 'utf8');
html = html.replace(/\{\{\s*url_for\s*\(\s*['"]static['"]\s*,\s*filename\s*=\s*['"]([^'"]+)['"]\s*\)\s*\}\}/g, 'static/$1');
fs.writeFileSync(path.join(DST_WWW, 'index.html'), html, 'utf8');
console.log('Written www/index.html');

// Copy static assets
['NoSleep.js', 'firebase-config.js', 'style.css'].forEach(function (name) {
  const src = path.join(SRC_STATIC, name);
  if (fs.existsSync(src)) {
    fs.copyFileSync(src, path.join(DST_STATIC, name));
    console.log('Copied static/' + name);
  }
});

// Beep asset for native audio: create a minimal placeholder if missing
const beepPath = path.join(DST_SOUNDS, 'beep.wav');
if (!fs.existsSync(beepPath)) {
  // Minimal 44-byte WAV header + a few silent samples so the file exists; replace with real beep for production
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(44 + 8820, 4); // file size - 8 (0.1s at 44100 16bit mono)
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);   // PCM
  header.writeUInt16LE(1, 22);   // mono
  header.writeUInt32LE(44100, 24);
  header.writeUInt32LE(88200, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write('data', 36);
  header.writeUInt32LE(8820, 40);
  const samples = Buffer.alloc(8820, 0); // 0.1s silence
  fs.writeFileSync(beepPath, Buffer.concat([header, samples]));
  console.log('Created www/sounds/beep.wav (placeholder – replace with real 800Hz beep for cues)');
}

// Ensure iOS native-audio bundled path contains beep.wav
if (!fs.existsSync(DST_IOS_SOUNDS)) fs.mkdirSync(DST_IOS_SOUNDS, { recursive: true });
fs.copyFileSync(beepPath, path.join(DST_IOS_SOUNDS, 'beep.wav'));
console.log('Copied sounds/beep.wav -> ios/App/App/sounds/beep.wav');
// Also copy to app root resources to support plugins that resolve by filename only.
fs.copyFileSync(beepPath, path.join(DST_IOS_ROOT, 'beep.wav'));
console.log('Copied sounds/beep.wav -> ios/App/App/beep.wav');

// Copy optional long-form audio assets from static/sounds into www/sounds and iOS bundle.
['training_intro.mp3', 'training_guidance.mp3', 'theta.mp3', 'gamma.mp3'].forEach(function(name) {
  const src = path.join(SRC_SOUNDS, name);
  if (!fs.existsSync(src)) return;
  const dstWeb = path.join(DST_SOUNDS, name);
  const dstIosSounds = path.join(DST_IOS_SOUNDS, name);
  const dstIosRoot = path.join(DST_IOS_ROOT, name);
  fs.copyFileSync(src, dstWeb);
  fs.copyFileSync(src, dstIosSounds);
  fs.copyFileSync(src, dstIosRoot);
  console.log('Copied sounds/' + name + ' -> www/sounds, ios/App/App/sounds, ios/App/App');
});

// Copy optional baked-volume beep variants (beep_v0.wav ... beep_v100.wav, plus low-range half steps like beep_v0_5.wav).
if (fs.existsSync(SRC_SOUNDS)) {
  fs.readdirSync(SRC_SOUNDS)
    .filter(function(name) { return /^beep_v\d{1,3}(?:_5)?\.wav$/.test(name); })
    .forEach(function(name) {
      const src = path.join(SRC_SOUNDS, name);
      const dstWeb = path.join(DST_SOUNDS, name);
      const dstIosSounds = path.join(DST_IOS_SOUNDS, name);
      const dstIosRoot = path.join(DST_IOS_ROOT, name);
      fs.copyFileSync(src, dstWeb);
      fs.copyFileSync(src, dstIosSounds);
      fs.copyFileSync(src, dstIosRoot);
      console.log('Copied sounds/' + name + ' -> www/sounds, ios/App/App/sounds, ios/App/App');
    });
}

console.log('Done. Run: npx cap sync && npx cap open ios');

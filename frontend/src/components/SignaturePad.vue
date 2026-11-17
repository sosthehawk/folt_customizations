<script setup lang="ts">
// A place to sign: a canvas the approver draws on with a finger, a pen or a mouse. The value is a
// PNG data URI -- the shape approval_signatures accepts and stamps onto the step. Prefilled from
// the signature the user keeps, which they can accept as it is or clear and draw again.
//
// The pad is always white with dark ink, in both themes: the signature is printed on paper, and a
// dark pad would hide exactly what is being checked.
import { nextTick, onMounted, ref, watch } from "vue";

const props = defineProps<{ id: string; modelValue: string | null; label?: string }>();
const emit = defineEmits<{ "update:modelValue": [string | null] }>();

const canvas = ref<HTMLCanvasElement | null>(null);
let drawing = false;
let drew = false;
let last: { x: number; y: number } | null = null;

function ctx() {
  return canvas.value?.getContext("2d") ?? null;
}

function size() {
  const c = canvas.value;
  if (!c) return;
  const ratio = window.devicePixelRatio || 1;
  const rect = c.getBoundingClientRect();
  c.width = Math.max(1, Math.round(rect.width * ratio));
  c.height = Math.max(1, Math.round(rect.height * ratio));
  const g = ctx();
  if (!g) return;
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.lineWidth = 2.2;
  g.lineCap = "round";
  g.lineJoin = "round";
  g.strokeStyle = "#111827";
}

function paint(src: string | null) {
  const c = canvas.value;
  const g = ctx();
  if (!c || !g) return;
  g.clearRect(0, 0, c.width, c.height);
  if (!src) return;
  const img = new Image();
  img.onload = () => {
    const rect = c.getBoundingClientRect();
    const scale = Math.min(rect.width / img.width, rect.height / img.height, 1);
    const w = img.width * scale;
    const h = img.height * scale;
    g.drawImage(img, (rect.width - w) / 2, (rect.height - h) / 2, w, h);
  };
  img.src = src;
}

function point(event: PointerEvent) {
  const rect = canvas.value!.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function down(event: PointerEvent) {
  if (!canvas.value) return;
  event.preventDefault();
  canvas.value.setPointerCapture?.(event.pointerId);
  if (!drew) {
    // Drawing on a prefilled signature starts a new one rather than adding strokes to the old.
    paint(null);
    drew = true;
  }
  drawing = true;
  last = point(event);
  const g = ctx();
  g?.beginPath();
  g?.arc(last.x, last.y, 1, 0, Math.PI * 2);
  g?.fill();
}

function move(event: PointerEvent) {
  if (!drawing || !last) return;
  event.preventDefault();
  const p = point(event);
  const g = ctx();
  if (!g) return;
  g.beginPath();
  g.moveTo(last.x, last.y);
  g.lineTo(p.x, p.y);
  g.stroke();
  last = p;
}

function up() {
  if (!drawing) return;
  drawing = false;
  last = null;
  emit("update:modelValue", canvas.value?.toDataURL("image/png") ?? null);
}

function clear() {
  drew = true;
  paint(null);
  emit("update:modelValue", null);
}

onMounted(async () => {
  await nextTick();
  size();
  paint(props.modelValue);
});

// A new prefill (the document reloaded) replaces the pad unless the person has started drawing.
watch(
  () => props.modelValue,
  (value) => {
    if (!drew) paint(value);
  },
);
</script>

<template>
  <div class="pad-wrap">
    <canvas
      :id="id"
      ref="canvas"
      class="pad"
      role="img"
      :aria-label="label ?? 'Signature pad: draw your signature'"
      @pointerdown="down"
      @pointermove="move"
      @pointerup="up"
      @pointercancel="up"
      @pointerleave="up"
    />
    <div class="pad-foot">
      <span class="pad-hint">{{ modelValue ? "Signed" : "Sign above with your finger, pen or mouse" }}</span>
      <button type="button" class="btn btn-quiet" @click="clear">Clear</button>
    </div>
  </div>
</template>

<style scoped>
.pad-wrap {
  display: grid;
  gap: 0.35rem;
}
.pad {
  width: 100%;
  height: 160px;
  background: #ffffff;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-sm);
  touch-action: none;
  cursor: crosshair;
}
.pad-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.pad-hint {
  color: var(--muted);
  font-size: var(--text-xs);
}
</style>

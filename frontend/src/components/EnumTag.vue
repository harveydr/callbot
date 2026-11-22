<template>
  <el-tag :type="tagType" size="small">{{ text }}</el-tag>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  value: { type: [String, Number, Boolean], default: "" },
  labels: { type: Object, default: () => ({}) },
  typeMap: { type: Object, default: () => ({}) },
});

const text = computed(() => props.labels[props.value] ?? (props.value === "" ? "—" : String(props.value)));
const tagType = computed(() => props.typeMap[props.value] || "");
</script>
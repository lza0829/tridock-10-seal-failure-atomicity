<template>
  <div>
    <div class="hero">
      <div>
        <h1>失败清单</h1>
        <p>未通过结案校验的工单：整单已回滚，失败日保持可写、可重新装载，修正证据后可重试。</p>
      </div>
      <button @click="load">刷新</button>
    </div>
    <p class="msg">{{ err }}</p>
    <div class="panel" v-for="f in rows" :key="f.order_id">
      <h2>{{ f.order_no }} · 第 {{ f.attempt_no }} 次提交</h2>
      <p>
        <span class="tag bad">已整单回滚</span>
        <span class="muted" style="margin-left:8px">{{ f.finished_at }}</span>
        <span v-if="f.note" class="muted" style="margin-left:8px">备注：{{ f.note }}</span>
      </p>
      <p class="msg">{{ f.error }}</p>
      <h3>失败日（未过结案校验）</h3>
      <table>
        <thead><tr><th>到货日</th><th>原因</th></tr></thead>
        <tbody>
          <tr v-for="d in f.failed_days" :key="d.dock_day">
            <td><span class="tag bad">{{ d.dock_day }}</span></td>
            <td>{{ d.reason }}</td>
          </tr>
        </tbody>
      </table>
      <template v-if="f.rolled_back_days.length">
        <h3>被牵连回滚日（本身已通过校验，随整单回滚，未封印）</h3>
        <p>
          <span v-for="d in f.rolled_back_days" :key="d.dock_day" class="tag" style="margin-right:6px">
            {{ d.dock_day }} 已回滚
          </span>
        </p>
      </template>
    </div>
    <div class="panel" v-if="!rows.length && !err">
      <p class="muted">没有失败的结案工单。</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'

const rows = ref([])
const err = ref('')

async function load() {
  err.value = ''
  try { rows.value = await api('/close-orders/failures') } catch (e) { err.value = e.message }
}
onMounted(load)
</script>

<style scoped>
.muted { color: var(--muted); }
h3 { font-size: 0.95rem; margin: 12px 0 8px; }
</style>

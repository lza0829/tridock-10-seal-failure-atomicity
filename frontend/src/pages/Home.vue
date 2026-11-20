<template>
  <div>
    <div class="hero">
      <div>
        <h1>总览</h1>
        <p>当前到货日三腿行数、对账批次与结案状态。结案后当日预报/实收/回执不可再改。</p>
      </div>
      <button @click="load">刷新</button>
    </div>
    <div class="grid" v-if="d">
      <div class="stat"><div class="k">到货日</div><div class="v">{{ d.dock_day }}</div></div>
      <div class="stat"><div class="k">预报 / 实收 / 回执</div><div class="v">{{ d.expect_count }} / {{ d.receive_count }} / {{ d.confirm_count }}</div></div>
      <div class="stat"><div class="k">未决差额</div><div class="v">{{ d.pending_variance }}</div></div>
      <div class="stat">
        <div class="k">结案</div>
        <div class="v"><span class="tag" :class="d.sealed ? 'seal' : 'bad'">{{ d.sealed ? '已结案' : '未结案' }}</span></div>
      </div>
      <div class="stat"><div class="k">已结案到货日</div><div class="v">{{ d.sealed_day_count }}</div></div>
      <div class="stat"><div class="k">结案工单（成功 / 失败）</div><div class="v">{{ d.committed_order_count }} / {{ d.failed_order_count }}</div></div>
    </div>
    <div class="panel" v-if="d && d.latest_batch">
      <h2>最近对账批次</h2>
      <p>{{ d.latest_batch.batch_no }} · 对齐 {{ d.latest_batch.matched_count }} · 差额 {{ d.latest_batch.variance_count }} · {{ d.latest_batch.status }}</p>
    </div>
    <div class="panel" v-if="d && d.days && d.days.length">
      <h2>逐日状态</h2>
      <table>
        <thead><tr><th>到货日</th><th>预报 / 实收 / 回执</th><th>批次</th><th>写闸</th></tr></thead>
        <tbody>
          <tr v-for="r in d.days" :key="r.dock_day">
            <td>{{ r.dock_day }}</td>
            <td>{{ r.expect_count }} / {{ r.receive_count }} / {{ r.confirm_count }}</td>
            <td>{{ r.batch_no ? r.batch_no + ' · ' + r.batch_status : '—' }}</td>
            <td><span class="tag" :class="r.sealed ? 'seal' : 'ok'">{{ r.sealed ? '已关闸' : '可写' }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="msg">{{ err }}</p>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const d = ref(null)
const err = ref('')
async function load() {
  err.value = ''
  try { d.value = await api('/dashboard') } catch (e) { err.value = e.message }
}
onMounted(load)
</script>

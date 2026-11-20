<template>
  <div>
    <div class="hero">
      <div>
        <h1>结案工单</h1>
        <p>一次勾选多个到货日，单个工单整单提交。<b>整单原子</b>：任一日未过结案校验，整单回滚——所有日恢复提交前写闸与批态，不留半截封印。</p>
      </div>
      <button @click="load">刷新</button>
    </div>

    <div class="panel">
      <h2>可结案到货日（已跑三联对账）</h2>
      <table v-if="cands.open.length">
        <thead><tr><th></th><th>到货日</th><th>批次</th><th>对齐</th><th>差额</th><th>未决差额</th><th>校验</th></tr></thead>
        <tbody>
          <tr v-for="c in cands.open" :key="c.dock_day">
            <td>
              <input type="checkbox" :value="c.dock_day" v-model="picked" :disabled="c.pending_count > 0" />
            </td>
            <td>{{ c.dock_day }}</td>
            <td>{{ c.batch_no }}</td>
            <td>{{ c.matched_count }}</td>
            <td>{{ c.variance_count }}</td>
            <td>{{ c.pending_count }}</td>
            <td>
              <span class="tag" :class="c.pending_count ? 'bad' : 'ok'">{{ c.pending_count ? '差额未处置' : '可结案' }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无可结案的到货日，请先在「三联对账」跑批并处置差额。</p>
      <div class="row" style="margin-top:12px">
        <input v-model="note" placeholder="工单备注（可选）" style="min-width:260px" />
        <button class="warn" :disabled="!picked.length || busy" @click="submit">
          提交结案工单（{{ picked.length }} 日，整单原子）
        </button>
      </div>
      <p class="msg">{{ err }}</p>
    </div>

    <div class="panel" v-if="receipt">
      <h2>工单回执 {{ receipt.order_no }} · 第 {{ receipt.attempt_no }} 次提交</h2>
      <p>
        整单状态：
        <span class="tag" :class="receipt.status === 'committed' ? 'seal' : 'bad'">
          {{ receipt.status === 'committed' ? '已整单结案' : '已整单回滚' }}
        </span>
        <span v-if="receipt.error" class="msg" style="display:inline;margin-left:10px">{{ receipt.error }}</span>
      </p>
      <table>
        <thead><tr><th>到货日</th><th>结果</th><th>批次</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="d in receipt.days" :key="d.dock_day">
            <td>{{ d.dock_day }}</td>
            <td>
              <span class="tag" :class="dayTagClass(d.status)">{{ dayLabel(d.status) }}</span>
            </td>
            <td>{{ d.batch_no || (d.batch_id ? '#' + d.batch_id : '—') }}</td>
            <td>{{ d.reason || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="cands.sealed.length">
      <h2>已结案到货日</h2>
      <table>
        <thead><tr><th>到货日</th><th>批次</th><th>封印时间</th><th>结案工单</th></tr></thead>
        <tbody>
          <tr v-for="s in cands.sealed" :key="s.dock_day">
            <td>{{ s.dock_day }}</td>
            <td>{{ s.batch_no }}</td>
            <td>{{ s.sealed_at }}</td>
            <td>{{ s.order_id ? '工单 #' + s.order_id : '单日结案' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>工单记录</h2>
      <table v-if="orders.length">
        <thead><tr><th>单号</th><th>状态</th><th>日数</th><th>成功封印</th><th>创建</th><th>完成</th><th>逐日结果</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="o in orders" :key="o.id">
            <td>{{ o.order_no }}</td>
            <td><span class="tag" :class="o.status === 'committed' ? 'seal' : 'bad'">{{ o.status === 'committed' ? '已结案' : '已回滚' }}</span></td>
            <td>{{ o.day_count }}</td>
            <td>{{ o.sealed_count }}</td>
            <td>{{ o.created_at }}</td>
            <td>{{ o.finished_at || '—' }}</td>
            <td>
              <span v-for="d in o.days" :key="d.dock_day" class="tag" :class="dayTagClass(d.status)" style="margin-right:4px">
                {{ d.dock_day }} {{ dayLabel(d.status) }}
              </span>
            </td>
            <td>
              <button v-if="o.status === 'failed'" class="secondary" :disabled="busy" @click="retry(o.id)">重试</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">还没有结案工单。</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'

const cands = ref({ open: [], sealed: [] })
const orders = ref([])
const picked = ref([])
const note = ref('')
const receipt = ref(null)
const err = ref('')
const busy = ref(false)

function dayLabel(s) {
  return { sealed: '已封印', rejected: '已拒绝', rolled_back: '已回滚' }[s] || s
}
function dayTagClass(s) {
  return { sealed: 'seal', rejected: 'bad', rolled_back: '' }[s] || ''
}

async function load() {
  err.value = ''
  try {
    cands.value = await api('/close-orders/candidates')
    orders.value = await api('/close-orders')
  } catch (e) { err.value = e.message }
}

async function submit() {
  err.value = ''; busy.value = true; receipt.value = null
  try {
    // 整单一次提交：多日全部交给后端单个工单事务，前端绝不循环调单日接口。
    receipt.value = await api('/close-orders', {
      method: 'POST',
      body: JSON.stringify({ days: picked.value, note: note.value || null }),
    })
    picked.value = []
    await load()
  } catch (e) { err.value = e.message } finally { busy.value = false }
}

async function retry(orderId) {
  err.value = ''; busy.value = true; receipt.value = null
  try {
    receipt.value = await api(`/close-orders/${orderId}/retry`, { method: 'POST' })
    await load()
  } catch (e) { err.value = e.message } finally { busy.value = false }
}

onMounted(load)
</script>

<style scoped>
.muted { color: var(--muted); }
</style>

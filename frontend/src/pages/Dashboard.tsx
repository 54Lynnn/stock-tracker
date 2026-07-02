import { useEffect, useState } from 'react'
import { fetchStocks, fetchAnnouncements, getExportUrl, type Stock, type Announcement } from '../lib/api'

export default function Dashboard() {
  const [stocks, setStocks] = useState<Stock[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [announcements, setAnnouncements] = useState<Record<string, Announcement[]>>({})
  const [annLoading, setAnnLoading] = useState<Record<string, boolean>>({})
  const [showText, setShowText] = useState<Record<string, boolean>>({})

  useEffect(() => {
    fetchStocks()
      .then(setStocks)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function toggleStock(code: string) {
    if (expanded === code) {
      setExpanded(null)
      return
    }
    setExpanded(code)
    if (!announcements[code]) {
      setAnnLoading(prev => ({ ...prev, [code]: true }))
      try {
        const anns = await fetchAnnouncements(code)
        setAnnouncements(prev => ({ ...prev, [code]: anns }))
      } catch {
        setAnnouncements(prev => ({ ...prev, [code]: [] }))
      } finally {
        setAnnLoading(prev => ({ ...prev, [code]: false }))
      }
    }
  }

  function toggleText(id: string) {
    setShowText(prev => ({ ...prev, [id]: !prev[id] }))
  }

  const filtered = stocks.filter(s => {
    if (!search) return true
    const kw = search.toLowerCase()
    return s.stock_code.toLowerCase().includes(kw) || s.stock_name.toLowerCase().includes(kw)
  })

  return (
    <div className="min-h-screen bg-[#f5f6fa]">
      {/* Header */}
      <div className="bg-gradient-to-r from-[#0984e3] to-[#6c5ce7] text-white px-8 py-3 sticky top-0 z-50 flex items-center gap-6">
        <div>
          <h1 className="text-[22px] font-semibold">AI 公告雷达</h1>
          <div className="text-[13px] opacity-80 mt-0.5">数据来源：东财 / 巨潮资讯</div>
        </div>
        <div className="flex-1" />
        <a
          href={getExportUrl()}
          className="bg-white/25 text-white px-3.5 py-1.5 rounded-full text-[13px] no-underline backdrop-blur-sm hover:bg-white/40 transition-colors"
        >
          导出 CSV
        </a>
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="搜索股票代码或名称..."
          className="bg-white/25 text-white placeholder-white/70 px-3.5 py-2 rounded-full text-sm outline-none w-[220px] backdrop-blur-sm focus:bg-white/40 transition-colors"
        />
      </div>

      {/* Float bar */}
      {expanded && (
        <div className="fixed top-[60px] left-0 right-0 bg-[#f0f4ff] border-b-2 border-[#0984e3] px-8 py-2.5 z-[99] flex items-center justify-between shadow-md cursor-pointer" onClick={() => setExpanded(null)}>
          <div>
            <span className="font-bold text-[15px]">{stocks.find(s => s.stock_code === expanded)?.stock_name}</span>
            <span className="text-[#636e72] text-[13px] ml-2">{expanded}</span>
          </div>
          <span className="text-[#0984e3] text-[13px] font-semibold">点击折叠 ↑</span>
        </div>
      )}

      {/* Content */}
      <div className="max-w-[1200px] mx-auto my-5 px-4">
        {loading && <div className="text-center py-10 text-[#b2bec3]">加载中...</div>}
        {!loading && filtered.length === 0 && <div className="text-center py-10 text-[#b2bec3]">暂无公告数据</div>}

        {!loading && filtered.length > 0 && (
          <div className="rounded-lg overflow-hidden shadow-[0_1px_3px_rgba(0,0,0,0.08)]">
            <table className="w-full border-collapse bg-white">
              <thead>
                <tr className="bg-[#dfe6e9]">
                  <th className="text-left py-2.5 px-3.5 text-[13px] font-semibold text-[#636e72] w-[40%]">股票</th>
                  <th className="text-left py-2.5 px-3.5 text-[13px] font-semibold text-[#636e72] w-[15%]">7天</th>
                  <th className="text-left py-2.5 px-3.5 text-[13px] font-semibold text-[#636e72] w-[15%]">15天</th>
                  <th className="text-left py-2.5 px-3.5 text-[13px] font-semibold text-[#636e72] w-[15%]">30天</th>
                  <th className="text-left py-2.5 px-3.5 text-[13px] font-semibold text-[#636e72] w-[15%]">全部</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(s => (
                  <StockRow
                    key={s.stock_code}
                    stock={s}
                    expanded={expanded === s.stock_code}
                    anns={announcements[s.stock_code]}
                    annLoading={annLoading[s.stock_code]}
                    showText={showText}
                    onToggle={() => toggleStock(s.stock_code)}
                    onToggleText={toggleText}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function StockRow({
  stock: s,
  expanded,
  anns,
  annLoading,
  showText,
  onToggle,
  onToggleText,
}: {
  stock: Stock
  expanded: boolean
  anns?: Announcement[]
  annLoading?: boolean
  showText: Record<string, boolean>
  onToggle: () => void
  onToggleText: (id: string) => void
}) {
  return (
    <>
      <tr
        className={`border-b border-[#f0f0f0] cursor-pointer transition-colors ${expanded ? 'bg-[#f0f4ff] shadow-[0_2px_4px_rgba(0,0,0,0.1)]' : 'hover:bg-[#f8f9ff]'}`}
        onClick={onToggle}
      >
        <td className="py-2.5 px-3.5 text-sm">
          <span className="font-semibold">{s.stock_name}</span>
          <span className="text-[#636e72] text-xs ml-1.5">{s.stock_code}</span>
        </td>
        <td className="py-2.5 px-3.5 text-sm"><Badge value={`${s.valuable_7d}/${s.total_7d}`} color="bg-[#dfe6e9]" /></td>
        <td className="py-2.5 px-3.5 text-sm"><Badge value={`${s.valuable_15d}/${s.total_15d}`} color="bg-[#b2bec3] text-white" /></td>
        <td className="py-2.5 px-3.5 text-sm"><Badge value={`${s.valuable_30d}/${s.total_30d}`} color="bg-[#636e72] text-white" /></td>
        <td className="py-2.5 px-3.5 text-sm">{s.valuable_total}/{s.total}</td>
      </tr>
      {expanded && (
        <tr className="bg-[#fafbfc]">
          <td colSpan={5} className="p-0">
            {annLoading && <div className="p-5 text-[#b2bec3] text-sm">加载中...</div>}
            {!annLoading && anns?.length === 0 && <div className="p-5 text-[#b2bec3] text-sm">暂无公告</div>}
            {!annLoading && anns?.map(a => (
              <AnnItem key={a.ann_id} ann={a} showText={showText} onToggleText={onToggleText} />
            ))}
          </td>
        </tr>
      )}
    </>
  )
}

function Badge({ value, color }: { value: string; color: string }) {
  return (
    <span className={`inline-block min-w-[28px] px-2 py-0.5 rounded-[10px] text-xs font-semibold text-center ${color}`}>
      {value}
    </span>
  )
}

function AnnItem({ ann: a, showText, onToggleText }: { ann: Announcement; showText: Record<string, boolean>; onToggleText: (id: string) => void }) {
  const textId = `text-${a.ann_id}`
  const isShow = showText[textId]
  const tagParts = [a.ann_type_category, a.ann_type_tag].filter(Boolean)

  return (
    <div className="w-full py-3.5 px-5 border-b border-[#eee] last:border-b-0">
      <div className="font-semibold text-[15px] flex justify-between items-start gap-3 cursor-pointer" onClick={() => onToggleText(textId)}>
        <span>{a.title}</span>
        {tagParts.length > 0 && (
          <span className="bg-[#0984e3] text-white px-2.5 py-0.5 rounded-full text-xs font-semibold whitespace-nowrap flex-shrink-0 mt-0.5">
            {tagParts.join(' / ')}
          </span>
        )}
      </div>
      <div className="text-xs text-[#636e72] mt-0.5">
        {a.ann_date}
        {a.display_time_dfcf && ` (显示时间: ${a.display_time_dfcf})`}
        {a.url && (
          <>
            {' · '}
            <a href={a.url} target="_blank" rel="noopener noreferrer" className="text-[#0984e3] no-underline hover:underline">查看原文</a>
          </>
        )}
      </div>
      {a.summary && (
        <div className="mt-2 px-4 py-3 bg-[#e8f4fd] border-l-[3px] border-[#0984e3] rounded text-sm leading-[1.7] cursor-pointer" onClick={() => onToggleText(textId)}>
          {a.summary}
        </div>
      )}
      {a.clean_text && (
        <div className="mt-2.5 max-h-[400px] overflow-y-auto">
          <div className={`text-[13px] leading-[1.7] text-[#555] whitespace-pre-wrap ${isShow ? 'block' : 'hidden'}`}>
            {a.clean_text}
          </div>
        </div>
      )}
    </div>
  )
}

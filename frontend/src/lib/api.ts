export interface Stock {
  stock_code: string
  stock_name: string
  total: number
  valuable_total: number
  total_7d: number
  valuable_7d: number
  total_15d: number
  valuable_15d: number
  total_30d: number
  valuable_30d: number
}

export interface Announcement {
  ann_id: string
  title: string
  ann_date: string
  display_time_dfcf: string
  url: string
  summary: string
  clean_text: string
  ann_type_category: string
  ann_type_tag: string
}

export async function fetchStocks(): Promise<Stock[]> {
  const res = await fetch('/api/stocks')
  return res.json()
}

export async function fetchAnnouncements(code: string): Promise<Announcement[]> {
  const res = await fetch(`/api/announcements/${code}`)
  return res.json()
}

export function getExportUrl(): string {
  return '/api/export/csv'
}

/** Inflect's two rising strokes: a thought taking shape. */
export function InflectMark({size=32}:{size?:number}) {
  return <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
    <rect width="64" height="64" rx="18" fill="#244F43"/>
    <path d="M18 46V34c0-9 6-15 15-15h13" stroke="#EFF5EA" strokeWidth="6" strokeLinecap="round"/>
    <path d="M30 46v-9c0-4 3-7 7-7h9" stroke="#BFD4A5" strokeWidth="6" strokeLinecap="round"/>
    <circle cx="46" cy="46" r="3" fill="#BFD4A5"/>
  </svg>;
}

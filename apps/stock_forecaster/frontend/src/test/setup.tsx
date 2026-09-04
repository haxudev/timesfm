import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

vi.mock('react-plotly.js', () => ({
  default: ({ layout, data }: {
    layout?: { title?: { text?: string } }
    data?: unknown
  }) => (
    <div
      role="img"
      aria-label={layout?.title?.text ?? 'Chart'}
      data-series={JSON.stringify(data)}
    />
  ),
}))

Object.defineProperty(URL, 'createObjectURL', {
  value: vi.fn(() => 'blob:test'),
  writable: true,
})
Object.defineProperty(URL, 'revokeObjectURL', {
  value: vi.fn(),
  writable: true,
})

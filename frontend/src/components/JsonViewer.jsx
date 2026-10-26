import React, { useState, Component } from 'react'
import { ChevronRight, ChevronDown, Copy, Check } from 'lucide-react'

class JsonErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    console.warn('JsonViewer rendering error:', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="json-empty text-xs text-muted p-2">
          Unable to display formatted payload ({this.state.error?.message || 'Invalid data'})
        </div>
      )
    }
    return this.props.children
  }
}

function JsonNode({ keyName, value, depth = 0, defaultOpen = true }) {
  const [isOpen, setIsOpen] = useState(depth < 2 ? defaultOpen : false)

  if (value === null) {
    return (
      <div className="json-row" style={{ paddingLeft: `${depth * 16}px` }}>
        {keyName !== undefined && <span className="json-key">"{keyName}": </span>}
        <span className="json-null">null</span>
      </div>
    )
  }

  if (value === undefined) {
    return (
      <div className="json-row" style={{ paddingLeft: `${depth * 16}px` }}>
        {keyName !== undefined && <span className="json-key">"{keyName}": </span>}
        <span className="json-null" style={{ opacity: 0.6 }}>undefined</span>
      </div>
    )
  }

  if (typeof value === 'boolean') {
    return (
      <div className="json-row" style={{ paddingLeft: `${depth * 16}px` }}>
        {keyName !== undefined && <span className="json-key">"{keyName}": </span>}
        <span className="json-boolean">{value ? 'true' : 'false'}</span>
      </div>
    )
  }

  if (typeof value === 'number') {
    return (
      <div className="json-row" style={{ paddingLeft: `${depth * 16}px` }}>
        {keyName !== undefined && <span className="json-key">"{keyName}": </span>}
        <span className="json-number">{value}</span>
      </div>
    )
  }

  if (typeof value === 'string') {
    return (
      <div className="json-row" style={{ paddingLeft: `${depth * 16}px` }}>
        {keyName !== undefined && <span className="json-key">"{keyName}": </span>}
        <span className="json-string">"{value}"</span>
      </div>
    )
  }

  if (typeof value !== 'object') {
    return (
      <div className="json-row" style={{ paddingLeft: `${depth * 16}px` }}>
        {keyName !== undefined && <span className="json-key">"{keyName}": </span>}
        <span className="json-string">{String(value)}</span>
      </div>
    )
  }

  const isArray = Array.isArray(value)
  let entries = []
  try {
    entries = isArray
      ? (value || []).map((v, i) => [i, v])
      : Object.entries(value || {})
  } catch {
    entries = []
  }

  const isEmpty = entries.length === 0

  if (isEmpty) {
    return (
      <div className="json-row" style={{ paddingLeft: `${depth * 16}px` }}>
        {keyName !== undefined && <span className="json-key">"{keyName}": </span>}
        <span className="json-bracket">{isArray ? '[]' : '{}'}</span>
      </div>
    )
  }

  return (
    <div className="json-branch">
      <div
        className="json-row json-collapsible"
        style={{ paddingLeft: `${depth * 16}px` }}
        onClick={(e) => {
          e.stopPropagation()
          setIsOpen(!isOpen)
        }}
      >
        <span className="json-toggle">
          {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        {keyName !== undefined && <span className="json-key">"{keyName}": </span>}
        <span className="json-bracket">{isArray ? '[' : '{'}</span>
        {!isOpen && (
          <span className="json-preview">
            {' '}
            ... {entries.length} {isArray ? 'items' : 'keys'}{' '}
            <span className="json-bracket">{isArray ? ']' : '}'}</span>
          </span>
        )}
      </div>

      {isOpen && (
        <div className="json-children">
          {entries.map(([k, v]) => (
            <JsonNode key={k} keyName={isArray ? undefined : k} value={v} depth={depth + 1} />
          ))}
          <div className="json-row" style={{ paddingLeft: `${depth * 16}px` }}>
            <span className="json-bracket">{isArray ? ']' : '}'}</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function JsonViewer({ data, title = 'Data', maxHeight = '400px' }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    try {
      navigator.clipboard.writeText(JSON.stringify(data, null, 2))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // fallback
    }
  }

  if (data === undefined || data === null) {
    return <div className="json-empty">Not provided by backend</div>
  }

  return (
    <div className="json-viewer-container">
      <div className="json-viewer-header">
        <span className="json-viewer-title">{title}</span>
        <button
          type="button"
          className="btn-copy"
          onClick={handleCopy}
          title="Copy JSON to clipboard"
        >
          {copied ? <Check size={13} className="text-success" /> : <Copy size={13} />}
          <span>{copied ? 'Copied' : 'Copy JSON'}</span>
        </button>
      </div>
      <div className="json-viewer-body" style={{ maxHeight }}>
        <JsonErrorBoundary>
          <JsonNode value={data} depth={0} defaultOpen={true} />
        </JsonErrorBoundary>
      </div>
    </div>
  )
}

import React, { Component } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo)
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="p-4 m-2 rounded bg-red-950/20 border border-red-500/30 text-red-300">
          <div className="flex items-center gap-2 mb-2 font-medium text-sm">
            <AlertTriangle size={16} className="text-red-400" />
            <span>{this.props.fallbackTitle || 'Component render error'}</span>
          </div>
          <p className="text-xs text-red-400/80 mb-3 font-mono">
            {this.state.error?.message || 'An unexpected rendering error occurred.'}
          </p>
          <button
            type="button"
            className="btn-action btn-sm flex items-center gap-1.5"
            onClick={this.handleReset}
          >
            <RefreshCw size={12} />
            <span>Try Reloading Component</span>
          </button>
        </div>
      )
    }

    return this.props.children
  }
}

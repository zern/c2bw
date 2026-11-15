import { ref } from 'vue'

export const isBridgeReady = ref(false)

let connectionPromise = null

export function initBridge() {
  if (connectionPromise) {
    return connectionPromise
  }

  connectionPromise = new Promise((resolve) => {
    let settled = false
    const onReady = () => finish(true)
    const finish = (ready) => {
      if (settled) return
      settled = true
      window.removeEventListener('pywebviewready', onReady)
      document.removeEventListener('pywebviewready', onReady)
      isBridgeReady.value = ready
      resolve(ready)
    }

    // 1. Check if pywebview already injected
    if (window.pywebview && window.pywebview.api) {
      finish(true)
      return
    }

    // 2. Listen to pywebviewready
    window.addEventListener('pywebviewready', onReady, { once: true })
    document.addEventListener('pywebviewready', onReady, { once: true })

    // 3. Fallback for browser HTTP dev server mode or timeout
    setTimeout(() => {
      if (settled) return
      if (window.pywebview && window.pywebview.api) {
        finish(true)
      } else if (window.location && window.location.protocol.startsWith('http')) {
        finish(true)
      } else {
        finish(false)
      }
    }, 1500)
  })

  return connectionPromise
}

export async function callApi(method, ...args) {
  // If pywebview api is available
  if (window.pywebview && window.pywebview.api && typeof window.pywebview.api[method] === 'function') {
    try {
      return await window.pywebview.api[method](...args)
    } catch (err) {
      console.error(`[pywebview.api.${method}] error:`, err)
      throw err
    }
  }

  // Fallback to HTTP REST API
  try {
    const res = await fetch(`/api/${method}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ args })
    })
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${res.statusText}`)
    }
    return await res.json()
  } catch (err) {
    console.error(`[HTTP API /api/${method}] error:`, err)
    throw err
  }
}

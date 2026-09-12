import { ref } from 'vue'

export const isBridgeReady = ref(false)

let connectionPromise = null

export function initBridge() {
  if (connectionPromise) {
    return connectionPromise
  }

  connectionPromise = new Promise((resolve) => {
    // 1. Check if pywebview already injected
    if (window.pywebview && window.pywebview.api) {
      isBridgeReady.value = true
      resolve(true)
      return
    }

    // 2. Listen to pywebviewready
    const onReady = () => {
      isBridgeReady.value = true
      resolve(true)
    }
    window.addEventListener('pywebviewready', onReady, { once: true })
    document.addEventListener('pywebviewready', onReady, { once: true })

    // 3. Fallback for browser HTTP dev server mode
    setTimeout(() => {
      if (!isBridgeReady.value) {
        if (window.location && window.location.protocol.startsWith('http')) {
          // In HTTP dev server mode
          isBridgeReady.value = true
          resolve(true)
        } else if (window.pywebview && window.pywebview.api) {
          isBridgeReady.value = true
          resolve(true)
        }
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

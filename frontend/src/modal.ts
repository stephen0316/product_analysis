import { useLayoutEffect, useRef } from 'react'

const focusableSelector = 'button, [href], input, select, textarea, [tabindex], [contenteditable="true"]'
type ModalLayer = { element: HTMLElement; close: () => void; canClose: () => boolean; initial?: string }
const layers: ModalLayer[] = []
let restoreIsolation: (() => void) | undefined
let unlockScroll: (() => void) | undefined

function focusable(element: HTMLElement) {
  return Array.from(element.querySelectorAll<HTMLElement>(focusableSelector)).filter(item =>
    item.tabIndex >= 0 && !item.matches(':disabled') && !item.closest('[inert], [hidden]') &&
    item.getClientRects().length > 0 && getComputedStyle(item).visibility !== 'hidden')
}

function focusInside(layer: ModalLayer, initial = false) {
  const preferred = initial && layer.initial ? layer.element.querySelector<HTMLElement>(layer.initial) : null
  ;(preferred && !preferred.matches(':disabled') ? preferred : focusable(layer.element)[0] || layer.element).focus({ preventScroll: true })
}

function isolateBackground() {
  restoreIsolation?.()
  const saved: { element: HTMLElement; inert: boolean }[] = []
  let branch = layers.at(-1)?.element
  while (branch?.parentElement) {
    for (const sibling of branch.parentElement.children) {
      if (sibling !== branch && sibling instanceof HTMLElement) {
        saved.push({ element: sibling, inert: sibling.inert })
        sibling.inert = true
      }
    }
    branch = branch.parentElement
  }
  restoreIsolation = () => { for (const item of saved) item.element.inert = item.inert }
}

/** One stack for all dialogs/drawers, including nested layers and StrictMode cleanup. */
export function mountModal(layer: ModalLayer) {
  const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
  if (!layers.length) {
    const bodyOverflow = document.body.style.overflow
    const rootOverflow = document.documentElement.style.overflow
    document.body.style.overflow = 'hidden'
    document.documentElement.style.overflow = 'hidden'
    unlockScroll = () => {
      document.body.style.overflow = bodyOverflow
      document.documentElement.style.overflow = rootOverflow
    }
  }
  layers.push(layer)
  isolateBackground()
  focusInside(layer, true)
  const isTop = () => layers.at(-1) === layer
  function keydown(event: KeyboardEvent) {
    if (!isTop() || event.defaultPrevented) return
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      if (layer.canClose()) layer.close()
    } else if (event.key === 'Tab') {
      const items = focusable(layer.element)
      const index = items.indexOf(document.activeElement as HTMLElement)
      if (!items.length) { event.preventDefault(); layer.element.focus(); return }
      if (index < 0 || (event.shiftKey ? index === 0 : index === items.length - 1)) {
        event.preventDefault()
        items[event.shiftKey ? items.length - 1 : 0].focus()
      }
    }
  }
  function containFocus() {
    if (isTop() && !layer.element.contains(document.activeElement)) focusInside(layer)
  }
  function preventBackgroundScroll(event: Event) {
    if (isTop() && !layer.element.contains(event.target as Node)) event.preventDefault()
  }
  document.addEventListener('keydown', keydown)
  document.addEventListener('focusin', containFocus)
  document.addEventListener('wheel', preventBackgroundScroll, { passive: false })
  document.addEventListener('touchmove', preventBackgroundScroll, { passive: false })
  // Async operations may disable/remove the focused control. Keep keyboard access inside.
  const observer = new MutationObserver(() => {
    if (isTop() && (!layer.element.contains(document.activeElement) || document.activeElement?.matches(':disabled'))) focusInside(layer)
  })
  observer.observe(layer.element, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'hidden'] })
  return () => {
    observer.disconnect()
    document.removeEventListener('keydown', keydown)
    document.removeEventListener('focusin', containFocus)
    document.removeEventListener('wheel', preventBackgroundScroll)
    document.removeEventListener('touchmove', preventBackgroundScroll)
    layers.splice(layers.indexOf(layer), 1)
    isolateBackground()
    if (!layers.length) { restoreIsolation?.(); restoreIsolation = undefined; unlockScroll?.(); unlockScroll = undefined }
    if (previous?.isConnected && !previous.closest('[inert]') && !previous.matches(':disabled')) previous.focus({ preventScroll: true })
    else if (layers.length) focusInside(layers.at(-1)!)
  }
}

export function useModal<T extends HTMLElement = HTMLDivElement>(onClose: () => void, options: { canClose?: boolean; initial?: string } = {}) {
  const ref = useRef<T>(null)
  const latest = useRef({ onClose, ...options })
  latest.current = { onClose, ...options }
  useLayoutEffect(() => {
    if (!ref.current) return
    return mountModal({ element: ref.current, close: () => latest.current.onClose(), canClose: () => latest.current.canClose !== false, initial: latest.current.initial })
  }, [])
  return ref
}

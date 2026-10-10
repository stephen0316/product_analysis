import { Children, isValidElement, useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactElement, type ReactNode } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { createPortal } from 'react-dom'

type SelectOption = { value: string; label: string; disabled?: boolean }

function optionText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(optionText).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) return optionText(node.props.children)
  return ''
}

type Props = {
  value: string
  children: ReactNode
  onChange: (event: { target: { value: string } }) => void
  label: string
  disabled?: boolean
  className?: string
}

export function SystemSelect({ value, children, onChange, label, disabled = false, className = '' }: Props) {
  const options: SelectOption[] = Children.toArray(children).filter(isValidElement).map(child => {
    const option = child as ReactElement<{ value?: string; children?: ReactNode; disabled?: boolean }>
    const optionLabel = optionText(option.props.children)
    return { value: String(option.props.value ?? optionLabel), label: optionLabel, disabled: option.props.disabled }
  })
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const [position, setPosition] = useState({ top: 0, left: 0, width: 220, maxHeight: 280 })
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const listId = useId()
  const selectedIndex = options.findIndex(option => option.value === value)
  const selected = options[selectedIndex]
  const isDisabled = disabled || options.length === 0

  function updatePosition() {
    const rect = triggerRef.current?.getBoundingClientRect()
    if (!rect) return
    const margin = 8
    const width = Math.min(Math.max(rect.width, 220), window.innerWidth - margin * 2)
    const left = Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin))
    const below = window.innerHeight - rect.bottom - margin
    const above = rect.top - margin
    const showAbove = below < 170 && above > below
    const maxHeight = Math.min(280, Math.max(100, showAbove ? above : below))
    const top = showAbove ? Math.max(margin, rect.top - maxHeight - 4) : rect.bottom + 4
    setPosition({ top, left, width, maxHeight })
  }

  function openMenu(index = selectedIndex) {
    if (isDisabled) return
    const next = Math.max(0, Math.min(index, options.length - 1))
    setActiveIndex(options[next]?.disabled ? options.findIndex(option => !option.disabled) : next)
    updatePosition()
    setOpen(true)
  }

  function closeMenu() { setOpen(false) }

  function choose(index: number) {
    const option = options[index]
    if (!option || option.disabled) return
    onChange({ target: { value: option.value } })
    closeMenu()
    triggerRef.current?.focus()
  }

  function move(direction: 1 | -1) {
    let next = activeIndex
    for (let i = 0; i < options.length; i++) {
      next = (next + direction + options.length) % options.length
      if (!options[next].disabled) { setActiveIndex(next); break }
    }
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (isDisabled) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) openMenu(selectedIndex < 0 ? 0 : selectedIndex)
      else move(event.key === 'ArrowDown' ? 1 : -1)
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      const index = event.key === 'Home'
        ? options.findIndex(option => !option.disabled)
        : options.map(option => !option.disabled).lastIndexOf(true)
      if (!open) openMenu(index)
      else if (index >= 0) setActiveIndex(index)
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (open) choose(activeIndex)
      else openMenu()
    } else if (event.key === 'Escape' && open) {
      event.preventDefault()
      closeMenu()
    } else if (event.key === 'Tab' && open) {
      closeMenu()
    } else if (open && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const index = options.findIndex(option => !option.disabled && option.label.toLocaleLowerCase().startsWith(event.key.toLocaleLowerCase()))
      if (index >= 0) setActiveIndex(index)
    }
  }

  useEffect(() => {
    if (!open) return
    updatePosition()
    function onPointerDown(event: PointerEvent) {
      if (!triggerRef.current?.contains(event.target as Node) && !listRef.current?.contains(event.target as Node)) closeMenu()
    }
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
      document.removeEventListener('pointerdown', onPointerDown)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const list = listRef.current
    const option = list?.querySelector<HTMLElement>(`[data-option-index="${activeIndex}"]`)
    if (!list || !option) return
    if (option.offsetTop < list.scrollTop) list.scrollTop = option.offsetTop
    else if (option.offsetTop + option.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTop = option.offsetTop + option.offsetHeight - list.clientHeight
    }
  }, [open, activeIndex])

  return <>
    <button
      type="button"
      ref={triggerRef}
      className={`system-select ${className}`.trim()}
      role="combobox"
      aria-label={label}
      aria-haspopup="listbox"
      aria-controls={open ? listId : undefined}
      aria-expanded={open}
      aria-activedescendant={open && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
      disabled={isDisabled}
      onClick={() => open ? closeMenu() : openMenu()}
      onKeyDown={onKeyDown}
    >
      <span className={!selected || selected.value === '' ? 'select-placeholder' : ''}>{selected?.label || '请选择'}</span>
      <ChevronDown size={16} aria-hidden="true" />
    </button>
    {open && createPortal(<div
      ref={listRef}
      id={listId}
      className="system-select-menu"
      role="listbox"
      aria-label={label}
      style={{ top: position.top, left: position.left, width: position.width, maxHeight: position.maxHeight }}
    >{options.map((option, index) => <div
      key={`${option.value}-${index}`}
      id={`${listId}-${index}`}
      data-option-index={index}
      className={'system-select-option' + (index === activeIndex ? ' active' : '') + (option.disabled ? ' disabled' : '')}
      role="option"
      aria-selected={option.value === value}
      aria-disabled={option.disabled || undefined}
      onMouseEnter={() => { if (!option.disabled) setActiveIndex(index) }}
      onClick={() => choose(index)}
    ><span>{option.label}</span>{option.value === value && <Check size={16} aria-hidden="true" />}</div>)}</div>, document.body)}
  </>
}

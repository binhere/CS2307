import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Scroll behaviour that honours the operating-system "reduce motion" setting, for
 * scrollTo/scrollIntoView calls (CSS `scroll-behavior` does not cover them).
 * @returns "auto" (an instant jump) when reduced motion is requested or unknown, else "smooth".
 */
export function getPreferredScrollBehavior(): ScrollBehavior {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "auto"
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"
}

/* Shared vocabulary and helpers for the Opportunities and Actions tabs.
 * The value lists mirror backend/app/models/strategy.py (LEVER_STATUSES,
 * ACTION_STATUSES, PRIORITIES); the API rejects anything else with a 400. */
import { useMemo } from 'react';
import useApi from '../../../components/intel/useApi';

export const LEVER_STATUSES = ['Identified', 'Under evaluation', 'Approved', 'Actioned', 'Rejected'];
export const ACTION_STATUSES = ['Not started', 'In progress', 'Blocked', 'Done'];
export const PRIORITIES = ['High', 'Medium', 'Low'];
export const SCORES = [1, 2, 3, 4, 5];

/* Tone per status, matching the StatusBadge palette (components/intel/Badges). */
const TONES = {
  identified: 'neutral',
  'under evaluation': 'warn',
  approved: 'info',
  actioned: 'good',
  rejected: 'struck',
  'not started': 'neutral',
  'in progress': 'info',
  blocked: 'bad',
  done: 'good',
};
export const statusTone = (s) => TONES[String(s || '').toLowerCase()] || 'neutral';

/* One stable key per lever, authored or custom (the bubble chart and the list share it). */
export const leverKey = (l) => (l.lever_id != null ? `a${l.lever_id}` : `c${l.custom_lever_id}`);

/* Same rule as the API's `plotted`. */
export const isPlotted = (l) => !!(l.applies && l.ease && l.savings_score);

export const strategyUrl = (path) => `/api/strategy${path}`;

/* Same rule as the API's `overdue`: a due date before today and not Done. */
export function isOverdue(a, today) {
  return !!(a.due_date && today && a.due_date < today && a.status !== 'Done');
}

export function countActions(actions, today) {
  const c = { total: actions.length, not_started: 0, in_progress: 0, blocked: 0, done: 0, overdue: 0 };
  actions.forEach((a) => {
    if (a.status === 'Not started') c.not_started += 1;
    else if (a.status === 'In progress') c.in_progress += 1;
    else if (a.status === 'Blocked') c.blocked += 1;
    else if (a.status === 'Done') c.done += 1;
    if (isOverdue(a, today)) c.overdue += 1;
  });
  return c;
}

/* Members of the team, for assignee pickers (the Team page's own endpoint). */
export function useTeamMembers(teamId) {
  const { data } = useApi(teamId ? `/api/teams/${teamId}/members` : null);
  return useMemo(() => (data || [])
    .map((m) => ({ id: m.user_id, name: m.display_name || m.email }))
    .sort((a, b) => a.name.localeCompare(b.name)), [data]);
}

export const todayIso = () => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

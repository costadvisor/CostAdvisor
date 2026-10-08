import { useState } from 'react';
import api, { formatApiError } from '../../api';
import { useApi } from '../../components/intel';

/* Status + owner selects for an adopted strategy category.
 * PUT /api/strategy/categories/{slug}?team_id= {status?, owner_user_id?} → the
 * landing row; the caller merges it into the page's record. */

export const CATEGORY_STATUSES = ['Not started', 'Active', 'On hold', 'Closed'];

export default function CategoryControls({ slug, teamId, record, onSaved }) {
  const { data: members } = useApi(teamId ? `/api/teams/${teamId}/members` : null);
  const [saving, setSaving] = useState(null);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  const save = async (body, field) => {
    setSaving(field);
    setError(null);
    setSaved(false);
    try {
      const { data } = await api.put(`/api/strategy/categories/${encodeURIComponent(slug)}`, body, {
        params: { team_id: teamId },
      });
      onSaved?.(data);
      setSaved(true);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setSaving(null);
    }
  };

  const ownerId = record?.owner?.id || '';
  const list = [...(members || [])].sort((a, b) =>
    String(a.display_name || a.email).localeCompare(String(b.display_name || b.email)));
  // Keep the current owner selectable even before the member list arrives.
  if (ownerId && !list.some((m) => m.user_id === ownerId)) {
    list.unshift({ user_id: ownerId, display_name: record.owner.name });
  }

  return (
    <>
      <label className="st-ctl">
        <span className="st-ctl-label">Status</span>
        <select className="ix-select" value={record?.status || 'Active'} disabled={!!saving}
          onChange={(e) => save({ status: e.target.value }, 'status')}>
          {CATEGORY_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      <label className="st-ctl">
        <span className="st-ctl-label">Owner</span>
        <select className="ix-select" value={ownerId} disabled={!!saving}
          onChange={(e) => save({ owner_user_id: e.target.value || null }, 'owner')}>
          <option value="">Unassigned</option>
          {list.map((m) => (
            <option key={m.user_id} value={m.user_id}>{m.display_name || m.email}</option>
          ))}
        </select>
      </label>
      <span className={`st-ctl-status${error ? ' is-error' : ''}`} role="status" aria-live="polite" title={error || undefined}>
        {saving ? 'Saving…' : error ? 'Not saved' : saved ? 'Saved' : ''}
      </span>
    </>
  );
}

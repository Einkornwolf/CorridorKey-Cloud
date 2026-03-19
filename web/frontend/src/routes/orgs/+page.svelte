<script lang="ts">
	import { onMount } from 'svelte';
	import { getStoredUser } from '$lib/auth';
	import { goto } from '$app/navigation';

	interface OrgInfo {
		org_id: string;
		name: string;
		owner_id: string;
		personal: boolean;
		created_at: number;
	}

	interface OrgMember {
		user_id: string;
		org_id: string;
		role: string;
		joined_at: number;
	}

	let orgs = $state<OrgInfo[]>([]);
	let loading = $state(true);
	let newOrgName = $state('');
	let creating = $state(false);
	let createError = $state('');

	// Detail view
	let selectedOrg = $state<OrgInfo | null>(null);
	let members = $state<OrgMember[]>([]);
	let membersLoading = $state(false);

	// Add member
	let addMemberUserId = $state('');
	let addMemberRole = $state('member');
	let addMemberError = $state('');

	const userId = getStoredUser()?.id ?? '';

	async function authFetch(path: string, opts?: RequestInit) {
		const token = localStorage.getItem('ck:auth_token');
		const headers: Record<string, string> = { 'Content-Type': 'application/json' };
		if (token) headers['Authorization'] = `Bearer ${token}`;
		const res = await fetch(path, { ...opts, headers });
		if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
		return res.json();
	}

	async function loadOrgs() {
		try {
			const res = await authFetch('/api/orgs');
			orgs = res.orgs;
		} catch {
			orgs = [];
		}
	}

	async function createOrg() {
		if (!newOrgName.trim()) return;
		creating = true;
		createError = '';
		try {
			await authFetch('/api/orgs', {
				method: 'POST',
				body: JSON.stringify({ name: newOrgName.trim() })
			});
			newOrgName = '';
			await loadOrgs();
		} catch (e) {
			createError = e instanceof Error ? e.message : 'Failed to create org';
		} finally {
			creating = false;
		}
	}

	async function selectOrg(org: OrgInfo) {
		selectedOrg = org;
		membersLoading = true;
		try {
			const res = await authFetch(`/api/orgs/${org.org_id}/members`);
			members = res.members;
		} catch {
			members = [];
		} finally {
			membersLoading = false;
		}
	}

	async function addMember() {
		if (!selectedOrg || !addMemberUserId.trim()) return;
		addMemberError = '';
		try {
			await authFetch(`/api/orgs/${selectedOrg.org_id}/members`, {
				method: 'POST',
				body: JSON.stringify({ user_id: addMemberUserId.trim(), role: addMemberRole })
			});
			addMemberUserId = '';
			await selectOrg(selectedOrg);
		} catch (e) {
			addMemberError = e instanceof Error ? e.message : 'Failed to add member';
		}
	}

	async function removeMember(memberId: string) {
		if (!selectedOrg) return;
		try {
			await authFetch(`/api/orgs/${selectedOrg.org_id}/members/${encodeURIComponent(memberId)}`, {
				method: 'DELETE'
			});
			await selectOrg(selectedOrg);
		} catch { /* ignore */ }
	}

	async function changeRole(memberId: string, role: string) {
		if (!selectedOrg) return;
		try {
			await authFetch(`/api/orgs/${selectedOrg.org_id}/members/${encodeURIComponent(memberId)}/role`, {
				method: 'PATCH',
				body: JSON.stringify({ role })
			});
			await selectOrg(selectedOrg);
		} catch { /* ignore */ }
	}

	async function deleteOrg(org: OrgInfo) {
		try {
			await authFetch(`/api/orgs/${org.org_id}`, { method: 'DELETE' });
			selectedOrg = null;
			await loadOrgs();
		} catch { /* ignore */ }
	}

	function isOwner(org: OrgInfo): boolean {
		return org.owner_id === userId;
	}

	function back() {
		selectedOrg = null;
	}

	onMount(async () => {
		await loadOrgs();
		loading = false;
	});
</script>

<svelte:head>
	<title>Organizations — CorridorKey</title>
</svelte:head>

<div class="page">
	{#if loading}
		<div class="loading mono">Loading...</div>
	{:else if selectedOrg}
		<!-- Org detail view -->
		<div class="page-header">
			<button class="back-btn mono" onclick={back}>&larr; BACK</button>
			<h1 class="page-title">{selectedOrg.name}</h1>
			{#if selectedOrg.personal}
				<span class="org-badge mono personal">PERSONAL</span>
			{/if}
		</div>

		<div class="detail-layout">
			<!-- Members -->
			<section class="detail-card">
				<h2 class="card-title mono">MEMBERS</h2>
				{#if membersLoading}
					<p class="card-desc">Loading...</p>
				{:else}
					<div class="member-list">
						{#each members as m (m.user_id)}
							<div class="member-row">
								<span class="member-id mono">{m.user_id.substring(0, 20)}...</span>
								<span class="role-badge mono" data-role={m.role}>{m.role.toUpperCase()}</span>
								{#if isOwner(selectedOrg) && m.role !== 'owner'}
									<select
										class="role-select mono"
										value={m.role}
										onchange={(e) => changeRole(m.user_id, (e.target as HTMLSelectElement).value)}
									>
										<option value="member">member</option>
										<option value="admin">admin</option>
									</select>
									<button class="btn-icon" onclick={() => removeMember(m.user_id)} title="Remove">
										<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" stroke-width="1.5"/></svg>
									</button>
								{/if}
							</div>
						{/each}
					</div>

					{#if isOwner(selectedOrg)}
						<div class="add-member">
							<h3 class="sub-title mono">ADD MEMBER</h3>
							{#if addMemberError}
								<div class="form-error mono">{addMemberError}</div>
							{/if}
							<div class="add-member-row">
								<input
									type="text"
									class="input mono"
									bind:value={addMemberUserId}
									placeholder="User ID"
								/>
								<select class="role-select mono" bind:value={addMemberRole}>
									<option value="member">member</option>
									<option value="admin">admin</option>
								</select>
								<button class="btn btn-primary mono" onclick={addMember}>ADD</button>
							</div>
						</div>
					{/if}
				{/if}
			</section>

			{#if isOwner(selectedOrg) && !selectedOrg.personal}
				<button class="btn btn-danger mono" onclick={() => deleteOrg(selectedOrg)}>
					DELETE ORGANIZATION
				</button>
			{/if}
		</div>
	{:else}
		<!-- Org list view -->
		<div class="page-header">
			<h1 class="page-title">Organizations</h1>
		</div>

		<div class="list-layout">
			<!-- Create -->
			<section class="detail-card">
				<h2 class="card-title mono">CREATE ORGANIZATION</h2>
				{#if createError}
					<div class="form-error mono">{createError}</div>
				{/if}
				<div class="create-row">
					<input
						type="text"
						class="input"
						bind:value={newOrgName}
						placeholder="Organization name"
					/>
					<button class="btn btn-primary mono" onclick={createOrg} disabled={creating}>
						{creating ? 'Creating...' : 'Create'}
					</button>
				</div>
			</section>

			<!-- List -->
			<section class="detail-card">
				<h2 class="card-title mono">YOUR ORGANIZATIONS <span class="count">{orgs.length}</span></h2>
				{#if orgs.length === 0}
					<p class="card-desc">No organizations yet.</p>
				{:else}
					<div class="org-list">
						{#each orgs as org (org.org_id)}
							<button class="org-row" onclick={() => selectOrg(org)}>
								<span class="org-name">{org.name}</span>
								{#if org.personal}
									<span class="org-badge mono personal">PERSONAL</span>
								{:else}
									<span class="org-badge mono team">TEAM</span>
								{/if}
								{#if isOwner(org)}
									<span class="owner-badge mono">OWNER</span>
								{/if}
								<svg class="chevron" width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M4 2l4 4-4 4" stroke="currentColor" stroke-width="1.5"/></svg>
							</button>
						{/each}
					</div>
				{/if}
			</section>
		</div>
	{/if}
</div>

<style>
	.page {
		padding: var(--sp-5) var(--sp-6);
		max-width: 640px;
		height: 100%;
		overflow-y: auto;
	}

	.loading { display: flex; align-items: center; justify-content: center; height: 40vh; color: var(--text-tertiary); }

	.page-header {
		display: flex; align-items: center; gap: var(--sp-3);
		margin-bottom: var(--sp-5); padding-bottom: var(--sp-3);
		border-bottom: 1px solid var(--border);
	}
	.page-title { font-size: 20px; font-weight: 700; }

	.back-btn {
		font-size: 11px; letter-spacing: 0.06em; color: var(--text-tertiary);
		background: none; border: 1px solid var(--border); border-radius: var(--radius-sm);
		padding: 4px 10px; cursor: pointer;
	}
	.back-btn:hover { color: var(--text-primary); border-color: var(--text-tertiary); }

	.list-layout, .detail-layout { display: flex; flex-direction: column; gap: var(--sp-4); }

	.detail-card {
		display: flex; flex-direction: column; gap: var(--sp-3);
		padding: var(--sp-4); background: var(--surface-2);
		border: 1px solid var(--border); border-radius: var(--radius-lg);
	}
	.card-title { font-size: 10px; font-weight: 600; letter-spacing: 0.12em; color: var(--accent); display: flex; align-items: center; gap: var(--sp-2); }
	.card-desc { font-size: 13px; color: var(--text-secondary); }
	.count { font-size: 9px; background: var(--surface-4); padding: 1px 6px; border-radius: 8px; color: var(--text-secondary); }
	.sub-title { font-size: 10px; letter-spacing: 0.1em; color: var(--text-tertiary); margin-top: var(--sp-2); }

	.org-list { display: flex; flex-direction: column; gap: 1px; border-radius: var(--radius-md); overflow: hidden; }
	.org-row {
		display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-3) var(--sp-4);
		background: var(--surface-3); border: none; cursor: pointer; text-align: left;
		color: var(--text-primary); font-size: 14px; width: 100%; transition: background 0.1s;
	}
	.org-row:hover { background: var(--surface-4); }
	.org-name { flex: 1; }
	.chevron { color: var(--text-tertiary); flex-shrink: 0; }

	.org-badge { font-size: 10px; letter-spacing: 0.06em; padding: 2px 8px; border-radius: 3px; }
	.org-badge.personal { background: var(--accent-muted); color: var(--accent-dim); }
	.org-badge.team { background: var(--secondary-muted); color: var(--secondary); }
	.owner-badge { font-size: 9px; letter-spacing: 0.06em; color: var(--accent); opacity: 0.6; }

	.member-list { display: flex; flex-direction: column; gap: var(--sp-2); }
	.member-row { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-2) 0; }
	.member-id { flex: 1; font-size: 12px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; }

	.role-badge { font-size: 10px; letter-spacing: 0.06em; padding: 2px 8px; border-radius: 3px; font-weight: 600; }
	.role-badge[data-role="owner"] { background: rgba(255, 242, 3, 0.12); color: var(--accent); }
	.role-badge[data-role="admin"] { background: rgba(206, 147, 216, 0.12); color: var(--state-masked); }
	.role-badge[data-role="member"] { background: rgba(93, 216, 121, 0.12); color: var(--state-complete); }

	.role-select {
		font-size: 11px; padding: 3px 6px; background: var(--surface-3);
		border: 1px solid var(--border); border-radius: var(--radius-sm);
		color: var(--text-secondary); cursor: pointer; outline: none;
	}
	.role-select:focus { border-color: var(--accent); }

	.btn-icon {
		background: none; border: none; cursor: pointer; color: var(--text-tertiary);
		padding: 2px; border-radius: 3px; display: flex; align-items: center;
	}
	.btn-icon:hover { color: var(--state-error); }

	.create-row, .add-member-row { display: flex; gap: var(--sp-2); align-items: center; }
	.input {
		flex: 1; padding: 8px 12px; background: var(--surface-3);
		border: 1px solid var(--border); border-radius: var(--radius-sm);
		color: var(--text-primary); font-size: 14px; outline: none; font-family: inherit;
	}
	.input:focus { border-color: var(--accent); }
	.input::placeholder { color: var(--text-tertiary); }

	.btn { font-size: 11px; letter-spacing: 0.06em; padding: 8px 14px; border: none; border-radius: var(--radius-sm); cursor: pointer; transition: all 0.15s; flex-shrink: 0; }
	.btn:disabled { opacity: 0.4; cursor: not-allowed; }
	.btn-primary { background: var(--accent); color: #000; font-weight: 600; }
	.btn-primary:hover:not(:disabled) { background: #fff; }
	.btn-danger { background: transparent; border: 1px solid rgba(255, 82, 82, 0.3); color: var(--state-error); align-self: flex-start; }
	.btn-danger:hover { background: rgba(255, 82, 82, 0.1); }

	.form-error { padding: var(--sp-2) var(--sp-3); background: rgba(255, 82, 82, 0.08); border: 1px solid rgba(255, 82, 82, 0.2); border-radius: 6px; font-size: 12px; color: var(--state-error); }

	.add-member { border-top: 1px solid var(--border); padding-top: var(--sp-3); display: flex; flex-direction: column; gap: var(--sp-2); }
</style>

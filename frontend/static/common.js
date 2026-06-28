/* 公共工具：API 封装、提示、跳转 */

const API = {
    /** GET 请求 */
    async get(url) {
        const res = await fetch(url, { credentials: 'same-origin' });
        return this._handle(res);
    },
    /** POST 请求 */
    async post(url, body) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
            credentials: 'same-origin',
        });
        return this._handle(res);
    },
    /** POST 上传文件 */
    async upload(url, formData) {
        const res = await fetch(url, {
            method: 'POST',
            body: formData,
            credentials: 'same-origin',
        });
        return this._handle(res);
    },
    /** PUT 请求 */
    async put(url, body) {
        const res = await fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
            credentials: 'same-origin',
        });
        return this._handle(res);
    },
    /** DELETE 请求 */
    async del(url) {
        const res = await fetch(url, {
            method: 'DELETE',
            credentials: 'same-origin',
        });
        return this._handle(res);
    },
    async _handle(res) {
        let data;
        try { data = await res.json(); }
        catch { data = {}; }
        if (!res.ok) {
            const msg = data.error || `请求失败 (${res.status})`;
            const err = new Error(msg);
            err.data = data;
            err.status = res.status;
            throw err;
        }
        return data;
    },
};

/** 消息提示 */
function toast(msg, type = 'success', duration = 2500) {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), duration);
}

/** 跳转到指定页面（带登录检查） */
async function requireLogin() {
    try {
        const me = await API.get('/api/auth/me');
        return me;
    } catch {
        window.location.href = '/';
        return null;
    }
}

/** 登出 */
async function logout() {
    await API.post('/api/auth/logout');
    window.location.href = '/';
}

/** 根据角色跳转到对应工作台 */
function redirectByRole(role) {
    const map = { business: '/business.html', legal: '/legal.html', admin: '/admin.html' };
    window.location.href = map[role] || '/';
}

/** HTML 转义 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/** 状态中文映射 */
const STATUS_MAP = {
    submitted_to_legal: '待法务处理',
    reviewing: '审核中',
    confirmed: '已确认',
    archived: '已归档',
};

function statusText(status) {
    return STATUS_MAP[status] || status;
}

function statusTag(status) {
    return `<span class="status-tag ${status}">${statusText(status)}</span>`;
}

/** 格式化时间 */
function fmtTime(s) {
    if (!s) return '';
    return s.replace('T', ' ').slice(0, 19);
}

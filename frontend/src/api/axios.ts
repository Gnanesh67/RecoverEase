import axios from "axios";

const api = axios.create({
    baseURL: import.meta.env.VITE_API_URL || "/api",
    timeout: 45000,
    headers: { "Content-Type": "application/json" },
});

// Inject Bearer token on every request
api.interceptors.request.use((config) => {
    const token = localStorage.getItem("rc_token");
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

// Handle 401 globally
api.interceptors.response.use(
    (response) => response,
    (error) => {
        if (axios.isAxiosError(error)) {
            // Surface backend error message
            let msg = error.response?.data?.error;
            const detail = error.response?.data?.detail;
            if (!msg && detail) {
                if (Array.isArray(detail)) {
                    msg = detail.map((d: any) => (d.loc ? `${d.loc.slice(-1)[0]}: ${d.msg}` : d.msg || JSON.stringify(d))).join("; ");
                } else if (typeof detail === "object") {
                    msg = JSON.stringify(detail);
                } else {
                    msg = String(detail);
                }
            }
            if (!msg) {
                msg = error.message;
            }

            if (error.response?.status === 401) {
                // Only redirect if this is NOT a login/register request
                // (those 401s should just show an error toast, not force a redirect)
                const url = error.config?.url || "";
                const isAuthRequest = url.includes("/auth/login") || url.includes("/auth/register");
                if (!isAuthRequest) {
                    localStorage.removeItem("rc_token");
                    window.location.href = "/login";
                }
                return Promise.reject(new Error(msg));
            }

            return Promise.reject(new Error(msg));
        }
        if (error.request && !error.response) {
            return Promise.reject(
                new Error("Cannot connect to server. Is the backend running?"),
            );
        }
        return Promise.reject(error);
    },
);

export default api;

# ultrabot/config/doctor.py  （关键摘录）

def run_doctor(config_path: Path, data_dir: Path | None = None,
               repair: bool = False) -> DoctorReport:
    """运行所有健康检查并返回报告。"""
    report = DoctorReport()
    report.checks.append(check_config_file(config_path))    # 1. 合法 JSON？
    report.checks.append(check_config_version(config))       # 2. 需要迁移？
    report.checks.append(check_providers(config))            # 3. API 密钥已设置？
    report.checks.append(check_workspace(config))            # 4. 工作空间存在？
    report.checks.append(check_sessions_dir(data_dir))       # 5. 会话目录正常？
    report.warnings = check_security(config)                 # 6-8. 安全警告
    if repair:
        apply_migrations(config)  # 自动修复可修复的问题
    return report

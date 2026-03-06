import argparse
import configparser
import subprocess
import tempfile
import warnings
import sys
from pathlib import Path
from urllib.error import HTTPError

import jenkins
import platformdirs
from urllib3.exceptions import InsecureRequestWarning

# Игнорируем предупреждения о небезопасном соединении
warnings.simplefilter('ignore', InsecureRequestWarning)

APP_NAME = "jenkins-logs"
APP_AUTHOR = "jenkins-log-parser"


def get_config_path():
    """Возвращает путь к файлу конфигурации используя platformdirs."""
    config_dir = Path(platformdirs.user_config_dir(APP_NAME, APP_AUTHOR))
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / 'config.ini'


def create_default_config():
    """Создает файл конфигурации с настройками по умолчанию."""
    config = configparser.ConfigParser()

    config['jenkins'] = {
        'url': 'https://jenkins.srpr.mos.ru',
        'username': 'larinav4',
        'token': ''  # Пустой токен по умолчанию
    }

    config['logs'] = {
        'path': '~/ditwork/ditlogs/'
    }

    config['proxy'] = {
        'url': ''  # Пустой URL означает "не использовать прокси"; пример: http://host:port
    }

    return config


def load_config():
    """Загружает конфигурацию из файла или создает новую."""
    config_path = get_config_path()
    config = configparser.ConfigParser()

    if config_path.exists():
        config.read(config_path)
        # Проверяем, что все необходимые секции и ключи присутствуют
        if not config.has_section('jenkins') or not config.has_section('logs'):
            print(f"Файл конфигурации поврежден:  {config_path}")
            config = create_default_config()
    else:
        print(f"Файл конфигурации не найден.  Создаю новый: {config_path}")
        config = create_default_config()

    return config, config_path


def save_config(config, config_path):
    """Сохраняет конфигурацию в файл."""
    with open(config_path, 'w') as f:
        config.write(f)


def setup_config():
    """Интерактивная настройка конфигурации."""
    config, config_path = load_config()

    print("=== Настройка Jenkins Log Parser ===")
    print(f"Файл конфигурации: {config_path}")
    print()

    # Настройка Jenkins
    print("--- Настройки Jenkins ---")
    current_url = config.get('jenkins', 'url', fallback='https://jenkins.srpr.mos. ru')
    new_url = input(f"URL Jenkins сервера [{current_url}]: ").strip()
    if new_url:
        config.set('jenkins', 'url', new_url)

    current_username = config.get('jenkins', 'username', fallback='')
    new_username = input(f"Имя пользователя [{current_username}]: ").strip()
    if new_username:
        config.set('jenkins', 'username', new_username)

    current_token = config.get('jenkins', 'token', fallback='')
    token_display = '*' * len(current_token) if current_token else 'не установлен'
    new_token = input(f"API токен [{token_display}]: ").strip()
    if new_token:
        config.set('jenkins', 'token', new_token)

    print()

    # Настройка путей
    print("--- Настройки путей ---")
    current_path = config.get('logs', 'path', fallback='~/ditwork/ditlogs/')
    new_path = input(f"Путь для сохранения логов [{current_path}]: ").strip()
    if new_path:
        config.set('logs', 'path', new_path)

    print()

    # Сохраняем конфигурацию
    save_config(config, config_path)
    print(f"✓ Конфигурация сохранена: {config_path}")

    # Проверяем подключение к Jenkins (если токен указан)
    if config.get('jenkins', 'token'):
        print("\nПроверяю подключение к Jenkins...")
        try:
            server = create_jenkins_server(config)
            version = server.get_version()
            print(f"✓ Подключение успешно! Версия Jenkins:  {version}")
        except Exception as e:
            print(f"✗ Ошибка подключения к Jenkins: {e}")
            print("Проверьте URL, имя пользователя и токен.")
    else:
        print("\n⚠ Токен не указан. Подключение к Jenkins невозможно.")
        print("Запустите 'python get_jenkins_logs.py --setup' для настройки токена.")


def show_config():
    """Показывает текущую конфигурацию."""
    config, config_path = load_config()

    print("=== Текущая конфигурация ===")
    print(f"Файл конфигурации: {config_path}")
    print()

    print("--- Jenkins ---")
    print(f"URL: {config.get('jenkins', 'url', fallback='не установлен')}")
    print(f"Пользователь: {config.get('jenkins', 'username', fallback='не установлен')}")

    token = config.get('jenkins', 'token', fallback='')
    if token:
        print(f"Токен: {'*' * len(token)} (установлен)")
    else:
        print("Токен:  не установлен")

    print()
    print("--- Логи ---")
    print(f"Путь: {config.get('logs', 'path', fallback='не установлен')}")


def create_jenkins_server(config):
    """Создает и возвращает экземпляр Jenkins-сервера на основе конфигурации."""
    jenkins_config = config['jenkins']
    token = jenkins_config.get('token')
    if not token:
        raise ValueError("Токен не установлен.  Запустите с параметром --setup для настройки.")

    server = jenkins.Jenkins(
        jenkins_config['url'],
        username=jenkins_config['username'],
        password=token
    )
    # Отключаем проверку SSL-сертификата
    server._session.verify = False

    # Настраиваем прокси из конфигурации (необязательно)
    proxy_url = config.get('proxy', 'url', fallback='').strip()
    if proxy_url:
        server._session.proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

    # Проверяем соединение
    server.get_version()
    return server


def get_job_build_history(server, job_name):
    """Получает историю номеров билдов для указанной джобы."""
    try:
        job_info = server.get_job_info(job_name, fetch_all_builds=True)
        return {i["number"] for i in job_info['builds']}
    except HTTPError as e:
        if e.code == 404:
            raise ValueError(f"Ошибка:  Джоб с именем '{job_name}' не найден.")
        else:
            raise
    except jenkins.NotFoundException:
        raise ValueError(f"Ошибка: Джоб с именем '{job_name}' не найден.")


def parse_build_numbers(builds_str, job_name, server):
    """
    Парсит строку с номерами билдов (например, 'latest', '1,2,3', '5-10')
    и возвращает список валидных номеров.
    """
    available_builds = get_job_build_history(server, job_name)
    if not available_builds:
        raise ValueError("Для данной джобы не найдено ни одного билда.")

    if builds_str == "latest":
        return [max(available_builds)]

    selected_builds = set()
    parts = builds_str.replace(" ", "").split(',')

    for part in parts:
        if '-' in part:
            try:
                start, end = map(int, part.split('-'))
                if start > end:
                    start, end = end, start  # обрабатываем обратный диапазон
                selected_builds.update(range(start, end + 1))
            except ValueError:
                raise ValueError(f"Неверный формат диапазона: '{part}'. Ожидается 'число-число'.")
        else:
            try:
                selected_builds.add(int(part))
            except ValueError:
                raise ValueError(f"Неверный номер билда: '{part}'.  Ожидается число.")

    # Проверяем, существуют ли запрошенные билды
    invalid_builds = selected_builds - available_builds
    if invalid_builds:
        raise ValueError(
            f"Неверные номера билдов: {sorted(list(invalid_builds))}. Доступные билды: {sorted(list(available_builds))[:10]}...")

    return sorted(list(selected_builds), reverse=True)


def get_logs(server, job_name, build_numbers):
    """Получает логи для указанных номеров билдов."""
    print(f"Запрашиваю логи для билдов: {build_numbers}...")
    logs = []
    for number in build_numbers:
        try:
            log = server.get_build_console_output(job_name, number)
            logs.append(log)
        except jenkins.NotFoundException:
            print(f"Предупреждение: Билд номер {number} для джобы '{job_name}' не найден.")
    return logs


def save_logs_to_file(logs, job_name, base_path_str):
    """Сохраняет логи в файл."""
    if not logs:
        print("Нет логов для сохранения.")
        return

    base_path = Path(base_path_str).expanduser()
    # Разделяем имя джобы на префикс и остальное имя для создания подпапки
    job_parts = job_name.split('/')
    prefix = job_parts[0] if len(job_parts) > 1 else 'other'

    log_dir = base_path / prefix
    log_dir.mkdir(parents=True, exist_ok=True)  # Создаем директорию, если ее нет

    log_file_path = log_dir / f"{'_'.join(job_parts)}.log"

    text_to_save = "\n\n--- END OF BUILD ---\n\n".join(logs)

    with open(log_file_path, "w", encoding="utf-8") as f:
        f.write(text_to_save)

    print(f"Логи сохранены в файл: {log_file_path}")


def show_logs_in_lnav(logs):
    """Отправляет логи в lnav через stdin (Linux/macOS) или в блокнот через временный файл (Windows)."""
    if not logs:
        print("Нет логов для отображения.")
        return

    print("Открываю логи в просмотрщике...")
    text_to_show = "\n\n--- END OF BUILD ---\n\n".join(logs).encode("utf-8")

    # На Windows lnav недоступен — пишем во временный файл и открываем блокнотом
    if sys.platform == "win32":
        tmp = tempfile.NamedTemporaryFile(
            mode='wb', suffix='.log', delete=False
        )
        try:
            tmp.write(text_to_show)
            tmp.close()
            subprocess.run(["notepad.exe", tmp.name], check=True)
        except FileNotFoundError:
            print("Ошибка: команда 'notepad.exe' не найдена.")
        except subprocess.CalledProcessError as e:
            print(f"Ошибка при открытии блокнота: {e}")
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        return

    # Linux / macOS — используем lnav
    try:
        subprocess.run(["lnav", "-"], input=text_to_show, check=True)
    except FileNotFoundError:
        print("Ошибка: команда 'lnav' не найдена.  Убедитесь, что lnav установлен и доступен в PATH.")
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при запуске lnav: {e}")


def main():
    """Главная функция скрипта."""
    parser = argparse.ArgumentParser(
        description="Загрузчик логов из Jenkins.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # Группа команд управления конфигурацией
    config_group = parser.add_mutually_exclusive_group()
    config_group.add_argument(
        "--setup",
        action='store_true',
        help="Настройка конфигурации (токен, пути и т.д.)"
    )
    config_group.add_argument(
        "--show-config",
        action='store_true',
        help="Показать текущую конфигурацию"
    )

    parser.add_argument("job_name", nargs='?', help="Имя джобы в Jenkins (например, 'prefix/fullname').")
    parser.add_argument(
        "-b", "--builds",
        type=str,
        default="latest",
        help="Номера билдов для загрузки.  Форматы:\n"
             "  - 'latest': последний билд (по умолчанию).\n"
             "  - '5':  один конкретный билд.\n"
             "  - '1,2,3': несколько билдов через запятую.\n"
             "  - '5-10': диапазон билдов.\n"
             "  - '1,5-10': можно комбинировать."
    )
    parser.add_argument("-l", "--lnav", action='store_true', help="Открыть логи в lnav вместо сохранения в файл.")

    args = parser.parse_args()

    #try:
    # Команды управления конфигурацией
    if args.setup:
        setup_config()
        return

    if args.show_config:
        show_config()
        return

    # Основная функциональность - получение логов
    if not args.job_name:
        parser.error("Требуется указать имя джобы или использовать --setup/--show-config")

    config, _ = load_config()

    server = create_jenkins_server(config)

    build_numbers = parse_build_numbers(args.builds, args.job_name, server)

    logs = get_logs(server, args.job_name, build_numbers)

    if args.lnav:
        show_logs_in_lnav(logs)
    else:
        log_path = config.get('logs', 'path', fallback='empty')
        if log_path == 'empty':
            print("Укажите путь для сохранения логов в конфигурационном файле.")
            return
        save_logs_to_file(logs, args.job_name, log_path)

    # except (ValueError, ConnectionError, FileNotFoundError) as e:
    #     print(f"Ошибка: {e}")
    #     sys.exit(1)
    # except KeyboardInterrupt:
    #     print("\nОперация прервана пользователем.")
    #     sys.exit(1)
    # except Exception as e:
    #     print(f"Произошла непредвиденная ошибка: {e}")
    #     sys.exit(1)


if __name__ == "__main__":
    main()
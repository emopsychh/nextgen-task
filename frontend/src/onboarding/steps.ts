export type TourPlacement = "top" | "bottom" | "left" | "right";

export type TourStep = {
  id: string;
  /** data-tour attribute value; null = centered tip */
  target: string | null;
  title: string;
  body: string;
  placement?: TourPlacement;
  /** Navigate before showing this step */
  route?: string | "home" | "client-workspace" | "client-projects";
};

export const AGENCY_STEPS: TourStep[] = [
  {
    id: "welcome",
    target: null,
    title: "Добро пожаловать в кабинет агентства",
    body: "Короткий тур подсветит реальные кнопки и панели. На этом шаге можно полностью пропустить обучение.",
  },
  {
    id: "rail",
    target: "tour-client-rail",
    title: "Рельса клиентов",
    body: "Здесь все привязанные клиентские порталы. Переключайтесь между ними одним кликом.",
    placement: "right",
    route: "home",
  },
  {
    id: "add-client",
    target: "tour-add-client",
    title: "Добавить клиента",
    body: "Плюс открывает экран подключения. Клиент ставит то же приложение на своём портале — роль «Клиент» назначается автоматически.",
    placement: "right",
    route: "home",
  },
  {
    id: "connect",
    target: "tour-connect-client",
    title: "Привязка портала",
    body: "Выберите портал и нажмите «Подключить». После этого клиент появится в рельсе слева.",
    placement: "bottom",
    route: "home",
  },
  {
    id: "workspace",
    target: "tour-new-project",
    title: "Проекты клиента",
    body: "В списке проектов создавайте модули работ («Новый проект»). В Bitrix это задачи внутри проекта компании.",
    placement: "bottom",
    route: "client-projects",
  },
  {
    id: "sidebar",
    target: "tour-sidebar",
    title: "Кабинет клиента слева",
    body: "В боковой панели — обзор, проекты, отчёты и бэклог выбранного клиента.",
    placement: "right",
    route: "client-workspace",
  },
  {
    id: "focus",
    target: "tour-agency-focus",
    title: "Что важно сейчас",
    body: "На обзоре — обращения по отчётам, активные проекты и то, что горит по срокам.",
    placement: "left",
    route: "client-workspace",
  },
  {
    id: "tasks-hint",
    target: "tour-sidebar",
    title: "Задачи и статусы",
    body: "Откройте проект → «Новая задача». Статус («Начать», «Завершить») двигаете вы — клиент ставит задачу агентству.",
    placement: "right",
    route: "client-workspace",
  },
  {
    id: "done",
    target: null,
    title: "Готово — можно работать",
    body: "Привяжите клиента, создайте проект и задачу. Чат и синхронизация с Bitrix подхватятся сами.",
  },
];

export const CLIENT_STEPS: TourStep[] = [
  {
    id: "welcome",
    target: null,
    title: "Добро пожаловать",
    body: "Это ваше пространство для задач агентству. Тур покажет кнопки на каждый день. На этом шаге обучение можно пропустить.",
  },
  {
    id: "sidebar",
    target: "tour-sidebar",
    title: "Ваш кабинет слева",
    body: "Слева — обзор, проекты и отчёты. Проекты создаёт агентство; вы работаете с задачами внутри них.",
    placement: "right",
    route: "home",
  },
  {
    id: "hours",
    target: "tour-deal-hours",
    title: "Пакет часов",
    body: "Сверху видно, сколько часов осталось в пакете сопровождения.",
    placement: "bottom",
    route: "home",
  },
  {
    id: "waiting",
    target: "tour-waiting-for-you",
    title: "Обзор пространства",
    body: "Здесь проекты в работе и недавно завершённые задачи — быстрый вход в нужный модуль.",
    placement: "top",
    route: "home",
  },
  {
    id: "status-note",
    target: "tour-sidebar",
    title: "Статусы — на стороне агентства",
    body: "Вы описываете задачу и срок. «Начать» и «Завершить» нажимает команда.",
    placement: "right",
    route: "home",
  },
  {
    id: "done",
    target: null,
    title: "Можно начинать",
    body: "Откройте проект и создайте задачу, когда нужна работа от команды.",
  },
];

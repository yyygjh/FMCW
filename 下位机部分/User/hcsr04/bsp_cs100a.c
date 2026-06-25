#include "bsp_cs100a.h"
#include "systick/bsp_SysTick.h"

// 定时器输入捕获用户自定义变量结构体定义
STRUCT_CAPTURE TIM_ICUserValueStructure = {0,0,0,0};

// 中断优先级配置
static void GENERAL_TIM_NVIC_Config(void)
{
    NVIC_InitTypeDef NVIC_InitStructure; 
    // 设置中断组为0
    NVIC_PriorityGroupConfig(NVIC_PriorityGroup_0);		
	// 设置中断来源
    NVIC_InitStructure.NVIC_IRQChannel = GENERAL_TIM_IRQn ;	
	// 设置主优先级为 0
    NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority = 0;	 
	// 设置抢占优先级为3
    NVIC_InitStructure.NVIC_IRQChannelSubPriority = 3;	
    NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
    NVIC_Init(&NVIC_InitStructure);
}

static void GENERAL_TIM_GPIO_Config(void) 
{
    GPIO_InitTypeDef GPIO_InitStructure;

    // 输入捕获通道 GPIO 初始化
    RCC_APB2PeriphClockCmd(ECHO_RCC_GPIO_CLK, ENABLE);
    /* 复用外设时钟使能 */
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_AFIO, ENABLE);
    
    GPIO_InitStructure.GPIO_Pin =  ECHO_GPIO_PIN;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IN_FLOATING;
    GPIO_Init(ECHO_GPIO_PORT, &GPIO_InitStructure);	
    
    GPIO_PinRemapConfig(GPIO_FullRemap_TIM2, ENABLE); 
    GPIO_PinRemapConfig(GPIO_Remap_SWJ_JTAGDisable , ENABLE); 
    
}

static void GENERAL_TIM_Mode_Config(void)
{
    // ===================== 变量全部移到最前面 =====================
    TIM_TimeBaseInitTypeDef  TIM_TimeBaseStructure;
    TIM_ICInitTypeDef TIM_ICInitStructure;
    // ==============================================================

    // 开启定时器时钟,即内部时钟CK_INT=72M
    GENERAL_TIM_APBxClock_FUN(GENERAL_TIM_CLK,ENABLE);
    
/*--------------------时基结构体初始化-------------------------*/	
	// 自动重装载寄存器的值，累计TIM_Period+1个频率后产生一个更新或者中断
	TIM_TimeBaseStructure.TIM_Period=GENERAL_TIM_PERIOD;	
	// 驱动CNT计数器的时钟 = Fck_int/(psc+1)
	TIM_TimeBaseStructure.TIM_Prescaler= GENERAL_TIM_PRESCALER;	
	// 时钟分频因子 ，配置死区时间时需要用到
	TIM_TimeBaseStructure.TIM_ClockDivision=TIM_CKD_DIV1;		
	// 计数器计数模式，设置为向上计数
	TIM_TimeBaseStructure.TIM_CounterMode=TIM_CounterMode_Up;		
	// 重复计数器的值，没用到不用管
	TIM_TimeBaseStructure.TIM_RepetitionCounter=0;	
	// 初始化定时器
	TIM_TimeBaseInit(GENERAL_TIMx, &TIM_TimeBaseStructure);

	/*--------------------输入捕获结构体初始化-------------------*/	
	// 配置输入捕获的通道，需要根据具体的GPIO来配置
	TIM_ICInitStructure.TIM_Channel = GENERAL_TIM_CHANNELx;
	// 输入捕获信号的极性配置
	TIM_ICInitStructure.TIM_ICPolarity = GENERAL_TIM_STRAT_ICPolarity;
	// 输入通道和捕获通道的映射关系，有直连和非直连两种
	TIM_ICInitStructure.TIM_ICSelection = TIM_ICSelection_DirectTI;
	// 输入的需要被捕获的信号的分频系数
	TIM_ICInitStructure.TIM_ICPrescaler = TIM_ICPSC_DIV1;
	// 输入的需要被捕获的信号的滤波系数
	TIM_ICInitStructure.TIM_ICFilter = 0;
	// 定时器输入捕获初始化
	TIM_ICInit(GENERAL_TIMx, &TIM_ICInitStructure);
	
	// 清除更新和捕获中断标志位
    TIM_ClearFlag(GENERAL_TIMx, TIM_FLAG_Update|GENERAL_TIM_IT_CCx);	
    // 开启更新和捕获中断  
	TIM_ITConfig (GENERAL_TIMx, TIM_IT_Update | GENERAL_TIM_IT_CCx, ENABLE );
	
	// 使能计数器
	TIM_Cmd(GENERAL_TIMx, ENABLE);   
}

/**
  * @brief  TRIG脚的GPIO配置
  * @param  无
  * @retval 无
  */
static void CS100A_TRIG_GPIO_Config(void)
{		
    GPIO_InitTypeDef  GPIO_InitStruct;

    /*开启GPIO外设时钟*/
    RCC_APB2PeriphClockCmd(TRIG_RCC_GPIO_CLK, ENABLE);
  
    /*选择要控制的GPIO引脚*/															   
    GPIO_InitStruct.GPIO_Pin = TRIG_GPIO_PIN;	

    /*设置引脚的输出类型为推挽输出*/
    GPIO_InitStruct.GPIO_Mode  = GPIO_Mode_Out_PP;  

    /*设置引脚速率为高速 */   
    GPIO_InitStruct.GPIO_Speed = GPIO_Speed_50MHz;

    /*调用库函数，使用上面配置的GPIO_InitStructure初始化GPIO*/
    GPIO_Init(TRIG_GPIO_PORT, &GPIO_InitStruct);	
    
}

/**
  * @brief  输出一个大于 10us 的高电平触发测距
  * @param  无
  * @retval 无
  */
void CS100A_TRIG(void)
{
	GPIO_SetBits(TRIG_GPIO_PORT,TRIG_GPIO_PIN);
	Delay_us(30);
	GPIO_ResetBits(TRIG_GPIO_PORT,TRIG_GPIO_PIN);
}

/**
  * @brief  超声波测距模块初始化
  * @param  无
  * @retval 无
  */
void CS100A_Init(void)
{
    GENERAL_TIM_GPIO_Config();
    GENERAL_TIM_NVIC_Config();
    CS100A_TRIG_GPIO_Config();
    GENERAL_TIM_Mode_Config();
}